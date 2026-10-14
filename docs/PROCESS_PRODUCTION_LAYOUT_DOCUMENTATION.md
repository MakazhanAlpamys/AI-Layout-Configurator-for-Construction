# Production Requirement Appendices in Process Documents

Checkpoint I.5.5.1 publishes complete original production requirement
appendices with fresh actual-room evidence. Scoped root, ordinary installed-wheel
and rendered-page verification pass, including fitting tighter common/path
caller controls. Complete Cycle I delivery and engineering acceptance remain
separate.

The explicit entry points consume the existing closed version `0.2`
[process/path source](PROCESS_INSTALLATION_REVIEW.md), using the opted
[fresh production review](PROCESS_PRODUCTION_LAYOUT_REVIEW.md):

```python
from layout_configurator.process_production_layout_documentation import (
    export_process_documentation_with_production_layout,
    validate_process_documentation_with_production_layout,
)

index = export_process_documentation_with_production_layout(
    "process-with-paths.json", "new-production-documents",
    include_windows=False,
)
qa = validate_process_documentation_with_production_layout(
    "process-with-paths.json", "new-production-documents",
)
```

Both entry points accept `limits=ProcessDocumentationLimits()`,
`path_limits=ProcessInstallationPathLimits()` and
`production_limits=ProductionLayoutLimits()`. The exporter accepts an actual
boolean `include_windows`; readback derives that explicit policy from the
pinned index. Window policy does not change the original component identities.

## Source and artifact contract

The source stays version `0.2`. The new document index and QA use version
`0.3`; existing version `0.1` and `0.2` document entry points remain separate.
The destination must be new, with this same closed inventory:

| File | Intended content |
| --- | --- |
| `source.json` | Exact original input bytes |
| `review.json` | Fresh common, supplied-path and actual production-room evidence |
| `documentation.pdf` | Source-bound vector plan, schedules and complete evidence appendices |
| `index.json` | Artifact hashes, page inventory, original identity and explicit delivery controls |
| `qa.json` | Independently recomputed artifact-consistency result |

The closed index has eighteen fields: the original fifteen document fields
plus `path_limits`, `production_limits` and `include_windows`. The original
nine component hashes, revision, variant and whole-file SHA identify one
process revision. The additional production report retains the original
building/layout/program hashes and the separately defined base-building hash.
Embedded programs, raw optional fields and integer/float source distinctions
must not be rebound or normalized to manufacture agreement.

The plan reuses the admitted installation body poses, supported axis sweeps,
separately bound clearance and exterior floor/obstacle inventory. It does not
generate a layout, find an installation path or introduce new route geometry.

## Complete requirement evidence

A production summary and one ordered appendix group per original requirement
are added to the inherited common/path sections. The new groups are
`production_layout` and `production_requirement`; the latter retains the
original requirement ID as its source ID through every continuation page.

Each requirement prints its original declaration and SHA, operation/source
IDs, source SHA, reason, basis and complete supplied usage-rights record.
Fresh evidence includes the local status, actual shared segments, maximum
single contiguous span, required span and overlap area. Distinct short spans
do not become one adequate contiguous contact.

A capped or omitted requirement inventory must reject publication before PDF
saving. A valid empty requirements array remains explicitly UNKNOWN; it is
not equivalent to proven production suitability. Known production and pressure
FAIL must remain visible beside unknown provenance or missing staging.

## Intended readback and control semantics

Readback first pins the actual index bytes. Its three closed, validated
control dictionaries combine caller and authored controls using per-field
minimums. Actual captured file sizes, combined bytes, aggregate JSON nodes,
PDF pages, resources, decoded page streams, operations and extracted text are
admitted before fresh source geometry. A changed index during effective
recapture must reject before constructors; final recapture must retain the
same index, source and artifact bytes.

After admission, the current original source is recomputed under those
effective controls. Saved report/QA verdicts and coherently rewritten hashes
cannot supply evidence. Complete printed words, visible on-page lettering and
actual source vectors must agree with that fresh evidence.

Fitting tighter common/path controls are admitted before the authored report
comparison. One fresh review runs under the effective minimum controls and
must retain complete admitted evidence. Only the following authored control
annotations are then derived independently from the pinned index:

- `reports.equipment_scenarios.evidence.limits`, using the declared common
  controls and the existing scenario-limit mapping.
- Each `installation_paths.reports[*].evidence.limits`, using the declared
  common/path controls and the existing standalone leaf-limit mapping.
- The installation workload fields `shared_source_geometry_vertex_limit`,
  `per_request_source_geometry_vertex_limit` and `shared_process_geometry_limit`,
  using those declared controls and the standalone vertex ceiling.

Every other field remains fresh and is compared exactly: verdicts, findings,
measured witnesses, original identities and actual workload counters. Readback
does not copy saved report annotations or rerun geometry under relaxed
controls. The historical document contracts retain their existing behavior.

## Verification

Root's foreground single-file run passed all sixteen opted-format tests and
54 subtests in 94.44 seconds. This includes fitting tighter controls without
artifact changes or relaxed rereview, coherent annotation/workload/witness
forgeries, known FAIL beside artifact PASS, complete and empty requirement
inventories, metadata races and actual persisted PDF checks.

A scoped counterfactual bypassed only the authored-annotation projection after
fresh admission. Three expected subtests failed with `Saved review differs` in
7.91 seconds. After the exact source was restored, the three focused tests and
ten subtests passed in 28.40 seconds. The historical version `0.1` document
file separately passed fourteen tests and 25 subtests in 38.79 seconds; the
version `0.2` path-document file passed ten tests/twelve subtests in 42.20 seconds.

A regular wheel from `c2a8f11` plus the three frozen LF source/test files runs
outside the checkout. Six publications cover clear and gap/pressure-conflicting
sources, whole embedded float identity, explicit windows, six complete original
requirements and an empty requirement inventory. All actual artifact checks
PASS while conflicts stay FAIL and empty requirements stay UNKNOWN. Fitting
tighter readback preserves the clear/conflicting source and artifact bytes
with zero rerenders. Coherently rehashed false-verdict and invisible-lettering
PDF copies reject without changing their originals. Historical report hashes,
public signatures, all forty-eight document defaults and saved `0.1`/`0.2`
package QA remain unchanged; no solver is called.

All 134 pages are rendered and inspected across thirteen contact sheets; nine
full pages separately cover original requirement lettering, conflict/empty
summaries, source plans, the sixth requirement and retained `30.0` scalar.
[Compact evidence](acceptance/c2a8f11-process-production-documents.json) records
source/archive/wheel/input/PDF identities and actual results. Raw evidence stays
local under `out/checkpoints/process-production-layout-documentation-i551/`.
No full suite, benchmark, background task or full CI dispatch is run here.

Artifact QA PASS establishes consistency with supplied evidence. The
documents still remain DRAFT with `ok=False`; full facility validation stays
UNKNOWN, engineering acceptance PENDING and authenticated approval UNKNOWN.
Production-enforcing generation, outer package attachment of this new format,
complete scenarios/3D and the Cycle I reference-facility review stay separate.

# Original Facility Packages with Production Requirement Documents

Checkpoint I.5.5.2 adds explicit original-bound production requirement
attachments. Root tests, regular installed-wheel readback and inspected PDF
evidence pass within the software scope documented below.

The attachment combines an independently supplied original facility with
version `0.3` [production requirement documents](PROCESS_PRODUCTION_LAYOUT_DOCUMENTATION.md).
Its process input remains the existing closed version `0.2`
[process/path source](PROCESS_INSTALLATION_REVIEW.md). The original source file
and original facility remain independent authorities throughout readback.

## Explicit API

```python
from layout_configurator.process_production_layout_facility_package import (
    export_process_facility_package_with_production_layout,
    validate_process_facility_package_with_production_layout,
)
from layout_configurator.process_facility_package import ProcessFacilityPackageLimits
from layout_configurator.process_installation_review import ProcessInstallationPathLimits
from layout_configurator.production_layout_review import ProductionLayoutLimits

manifest = export_process_facility_package_with_production_layout(
    "process-with-paths.json", "original-facility", "new-package",
    base_kind="orthogonal",
    include_windows=False,
    include_grid=False,
    include_groups=False,
    limits=ProcessFacilityPackageLimits(),
    path_limits=ProcessInstallationPathLimits(),
    production_limits=ProductionLayoutLimits(),
)
qa = validate_process_facility_package_with_production_layout(
    "process-with-paths.json", "original-facility", "new-package",
    limits=ProcessFacilityPackageLimits(),
    path_limits=ProcessInstallationPathLimits(),
    production_limits=ProductionLayoutLimits(),
)
```

The exporter inherits the existing three projection flags and adds the
separate production limits. The facility profile comes from the original
base; there is no profile replacement or arbitrary projection-options argument.
The validator derives the window policy and projection choices from pinned
saved metadata.

## Original source and closed inventories

The outer manifest and QA use version `0.3`. The closed outer manifest has
twenty fields: the original seventeen plus `path_limits`, `production_limits`
and `include_windows`. The process source stays version `0.2`, and its document
index/QA stay version `0.3` with their own closed contract.

| Root entry | Content |
| --- | --- |
| `facility/` | Byte-identical original facility files: fourteen for the orthogonal format or nine for the supported single-variant rectangular fixture |
| `process/` | Exactly `source.json`, `review.json`, `documentation.pdf`, `index.json` and `qa.json` |
| `manifest.json` | Closed child inventory, original identities, profile/projection binding and declared controls |
| `qa.json` | Fresh outer artifact-consistency evidence with separate base and process checks |

The original building, layout, equipment and routes must match in their whole
raw frames, including scalar types, optional clearance and embedded programs.
Drawing revision and variant must agree. The original facility profile has a
separate hash. Nine raw process component hashes remain distinct from the
production report's original building/layout/program hashes, its base-building
hash and the path reports' canonical model signatures.

The production base-building binding excludes only its own embedded program.
That separate binding does not authorize stripping the embedded program from
the original whole building to match a facility. A valid embedded `30.0`
program can pass independent source review while its attachment to a base
without that whole program rejects. Such incompatibility preserves both
original inputs and publishes no substitute normalized source.

Existing version `0.1` [facility/process APIs](PROCESS_FACILITY_PACKAGES.md) and
version `0.2` [path attachments](PROCESS_INSTALLATION_FACILITY_PACKAGES.md)
retain their versions, inventories, public signatures and defaults. The
fifty-nine outer-package limit fields and forty-eight document limit fields
remain unchanged; production controls use their separate existing class.

## Admission, minimum controls and metadata pins

Caller, outer and child limit dictionaries must be closed, contain actual
bounded integers and tighten the existing defaults. The effective per-field
minimum applies to the initial source review and child QA. Source admission
must retain the shared common/path/production geometry, vertex, node and UTF
work reservation before canonical constructors or nested source decoding.

Both children contribute to outer file, byte, JSON-node, PDF-page, extracted
text and operation ceilings. Independently fitting children cannot reset an
aggregate allowance. Original-base parser controls and the complete captured
process inventory must be admitted before fresh source or physical readback.
Extracted text counts include their line breaks.

The initial outer manifest bytes and child index bytes are pinned separately.
Effective metadata recapture must agree with both initial snapshots before
geometry, and final physical QA must retain the same snapshots. A different
valid tighter declaration introduced during recapture or geometry still rejects
that in-progress readback; later metadata cannot replace its original authority.

Export preparation also pins the initially admitted original source and base
bytes. These guards run before the parent model reader and late child export/
validation boundaries. A fitting source change or a whitespace-only original
base change after pure binding rejects before source constructors. Validation
retains its original whole-package snapshot through final physical readback.

Fitting stricter caller or outer controls must not rewrite the authored child.
Fresh source review runs under the effective minimum controls. Only the
documented scenario/leaf limit annotations and installation workload limit
annotations are derived from the pinned **child** common/path controls for
comparison with its authored report and PDF. Actual work counts, requirements,
source identities, measured geometry and findings remain freshly computed.
There is no relaxed source rereview or adoption of saved report annotations.

## Window policy and physical readback

`include_windows` must be an actual boolean and agree across the outer manifest,
child document index and original base projection policy. Grid/group projection
flags retain their existing orthogonal applicability. A rectangular base rejects
every true projection flag, including windows. These policies preserve source
identity and do not supply new transfer permission or approval.

Actual original-base geometry and actual child PDF text/paths are rechecked.
Every original production requirement retains its ID/SHA, operation/source IDs,
source SHA, reason, basis, rights and complete fresh contact/overlap witness.
Coherently rehashed PDF verdict changes, omitted or hidden requirement lettering,
saved witness changes and native geometry drift must reject. Cached positive QA
and enclosing hashes cannot authorize altered physical evidence.

The destination must be new. Existing, aliased and raced directories remain
protected. Source or original-base changes during child export, staging or
readback reject publication rather than replacing existing work.

## Conflict exercise and acceptance scope

The reference conflict source keeps the original valid touching-room layout
and its original required relation. Its edited top production program declares
that `entry` and `work` must remain separated. The expected evidence retains:

- Source binding PASS with a provenance/declaration FAIL for the contradictory
  original relation, plus the independently supplied UNKNOWN usage-rights issue.
- Actual forbidden-contact FAIL for a positive contiguous 6,000 mm span, with
  no required minimum span and zero interior overlap.
- An independent declared pressure-cycle FAIL and missing exterior staging.

The original native bases remain independently checkable under their original
constraints. The edited program is a supplied conflicting concept; its known
provenance FAIL must not be relabeled UNKNOWN to accommodate the fixture.

Outer artifact QA PASS establishes consistency with both original inputs and
fresh scoped evidence. It can coexist with concept FAIL or UNKNOWN. The saved
package remains DRAFT with `ok=False`; full facility/process/scenario and 3D
installation feasibility remain UNKNOWN, authenticated approval UNKNOWN and
engineering acceptance PENDING. Producer enforcement, complete discipline
design, receiving-tool review and Cycle I acceptance remain separate.

## Retained verification

The actual pre-admission source/base race produces two failing subtests before
the guard repair. The repaired focused method passes both cases in 9.37 seconds.
The final single-file run passes eleven tests and sixty subtests in 232.32
seconds. No historical source module is changed. The new file also exercises
both old API versions and their publication/readback behavior.

A regular wheel installed into a fresh target and imported from a foreign
working directory publishes five packages: clear and forbidden/pressure cases
in both native formats, plus a true-window orthogonal policy. Four stricter
readbacks preserve original files without rerendering. Coherently rehashed
false printed verdict and saved-span copies reject; a combined text cap that
fits each child independently rejects before constructors. Both solver aliases
are guarded, with zero calls.

Installed old/new wheels retain identical fresh QA for five saved `0.1`/`0.2`
packages, unchanged original files, public signatures and all fifty-nine outer/
forty-eight document limit defaults. These actual compatibility checks accompany
the new-file regressions; no redundant broad historical pytest run is added.

All 129 pages of ten distinct physical PDFs are rendered and inspected across
fifteen contact sheets; six full pages separately cover requirement lettering,
the conflict summary/witness/plan, true-window policy and the native main plan.
The unchanged rectangular main plan still has a room caption crossing an
equipment outline. General annotation placement and main-font portability
remain separate; the supplied base is not altered to hide that limitation.

[Compact evidence](acceptance/4ca8a39-process-production-packages.json) retains
exact source/archive/wheel/input/artifact identities and actual results. Raw
evidence stays local under
`out/checkpoints/process-production-layout-facility-package-i552/`.
No full suite, benchmark, background task or full CI dispatch is run here.

The pushed `8ca095e` source checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38623080447)
in thirty-nine seconds including setup. Full jobs are skipped. This is scoped
push evidence, with changed full acceptance still pending.

# Original Facility Packages with Supplied Installation Documents

Checkpoint I.5.4.3 explicitly attaches version `0.2`
[process/path documents](PROCESS_INSTALLATION_DOCUMENTATION.md) to an
independently supplied matching facility package. The original facility and
original process input remain external authorities for every readback.

```python
from layout_configurator.process_installation_facility_package import (
    export_process_facility_package_with_paths,
    validate_process_facility_package_with_paths,
)

manifest = export_process_facility_package_with_paths(
    "process-with-paths.json", "original-facility", "new-package",
    base_kind="orthogonal",
)
qa = validate_process_facility_package_with_paths(
    "process-with-paths.json", "original-facility", "new-package",
)
```

The root inventory remains `facility/`, `process/`, `manifest.json` and
`qa.json`. The facility child is copied byte for byte: fourteen files for the
orthogonal format, or the exact supplied single-variant rectangular inventory.
The process child keeps its five-file version `0.2` inventory. Exterior staging
does not expand or rewrite the original building. Existing version `0.1`
[attachment APIs](PROCESS_FACILITY_PACKAGES.md) retain their input contract,
versions, inventories, public signatures and limit fields.

Original whole building, layout, equipment and route frames, scalar types,
optional clearance, embedded program, drawing revision and variant must match.
The facility profile has a separate identity. Process component hashes and
the path's canonical model signatures retain their different contracts.
Original source bytes and each supplied request SHA remain visible throughout
the process child and fresh evidence.

The new outer manifest and QA have version `0.2`; the manifest separately
declares closed, tighten-only `path_limits`. Caller, outer and child limits are
admitted before the first fresh source/path geometry. Effective common source,
document and path controls reach both initial review and child QA. Valid
stricter declarations may differ while preserving unchanged evidence; their
minimum applies. A child's positive artifact check cannot reset the outer
combined file, byte, JSON-node, PDF-page or operation budget.

Metadata captured for admission must remain byte-identical through effective
recapture and final physical QA. A changed child input limit is rejected before
layout parsing. Source/base changes during child generation and staged QA also
reject. Existing, aliased and raced destinations remain protected by native
publication without replacement.

Readback checks actual facility geometry and actual process PDF paths/text
again. Coherently rehashed DXF drift or a shifted body sweep in PDF cannot be
authorized by rewritten enclosing hashes or cached QA. Known path collision
FAIL remains explicit beside accepted facility and document artifacts.

Artifact-consistency PASS approves agreement with both original inputs and
fresh scoped checks. The package remains DRAFT with `.ok == False`, full
facility/process/scenario and 3D installation feasibility UNKNOWN,
authenticated approval UNKNOWN and engineering acceptance PENDING. Producer
enforcement, path search, additional views and Cycle I benchmark and external
acceptance remain separate.

Fresh root sequential single-file verification passes ten tests/twenty-nine
subtests in 185.29 seconds and the eight historical package tests/fourteen
subtests in 118.15 seconds. The actual metadata-recapture RED reached layout
parsing once; the fixed case stops before parsing. One test's intentionally
changed raw numeric type required an explicitly updated canonical path-request
signature before it could independently exercise original-base disagreement.

A regular wheel from `e481922` plus the three frozen files runs four actual
clear/blocked attachments outside the checkout. All fourteen or nine original
facility files remain unchanged, each process child has nineteen pages, and
both child artifact checks PASS. Clear local 2D PASS retains process UNKNOWN;
the actual 700,000 mm2 moving-body collision retains FAIL. Historical saved
version `0.1` outer QA is exactly reproduced, all fifty-nine outer limit fields
and values remain unchanged, and both historical PDF contracts pass fresh QA.
No solver runs. All 106 pages across the four process and original facility
PDF sets were rendered/contact-inspected; two complete path plans and a legacy
main plan were also viewed individually.

The unchanged legacy main plans retain overlapping work/press labels in this
supplied fixture and emit Symbol/ArialUnicode fallback-font warnings in the
local renderer. Attachment consistency does not repair original drawing text
or establish font portability/receiving-tool acceptance.
[Compact evidence](acceptance/e481922-process-path-packages.json) records exact
source, wheel, input, profile, request and saved-PDF identities. Local proof is
retained under `out/checkpoints/process-installation-facility-package-i543/`.

The pushed `a8f1e44` checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38576986775)
in twenty-nine seconds including setup. Full acceptance jobs are skipped;
changed full acceptance remains separate.

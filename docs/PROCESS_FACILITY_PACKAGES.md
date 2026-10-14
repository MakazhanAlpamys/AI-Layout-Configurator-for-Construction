# Source-Bound Facility and Process Packages

Checkpoint I.5.3 links an existing independently checked facility
bundle with its [process documentation](PROCESS_DOCUMENTATION.md). Both
children retain their original file inventories. The outer package records
their common source identity and checks them again before publication.

```python
from layout_configurator.process_facility_package import (
    export_process_facility_package,
    validate_process_facility_package,
)

manifest = export_process_facility_package(
    "review-input.json", "original-facility", "new-package",
    base_kind="orthogonal",
)
qa = validate_process_facility_package(
    "review-input.json", "original-facility", "new-package",
)
```

The original review input and original facility directory remain external
authorities for subsequent readback. Supplying them again allows the checker
to reject rewritten outer hashes and altered copies. A manifest cannot create
physical validity or authorize a changed process verdict.

The source building, actual layout, equipment placements and supplied routes
must agree exactly. Original scalar types, supplied clearance fields and whole
embedded production programs participate in that comparison. Drawing revision
and integer variant must also agree. The facility profile has its own source
hash; it does not replace any of the nine process component hashes.

The document remains DRAFT. Process FAIL or UNKNOWN remains explicit alongside
the facility and document artifact checks. Source authentication, complete
process/scenario feasibility and engineering acceptance remain separate.

Supported kinds are `orthogonal` and single-variant `rectangular`. The outer
inventory is exactly `facility/`, `process/`, `manifest.json` and `qa.json`.
The fourteen-file orthogonal child and five-file process child remain intact;
rectangular child artifacts are captured exactly from their supplied manifest.
Optional window/grid/group projection controls participate in source binding.

Readback checks both the externally supplied original facility and the copied
child, then reruns actual physical base QA and process-document QA. Known
pressure FAIL can accompany positive artifact checks; it is never approved by
the accepted base. Whole embedded-program disagreement, omitted clearance,
different routes, revision drift and different original numeric types reject.
Native staged publication preserves existing or raced destinations and checks
original inputs before and after source-document generation and readback.

Additional limits bound 128 files, 16 MiB per file, 32 MiB original base,
64 MiB full package, eight MiB JSON, 262,144 aggregate JSON nodes/depth 64,
directory depth eight, 1,024 ZIP members/32 MiB expanded BCF, 20,000 IFC
entities and 200,000 DXF pairs. Inherited document bounds also apply. Source
JSON lexical work is charged before decoding; PDF resource/stream/operation
bounds precede child checks. Limits only tighten.

Fresh root single-file verification passes eight tests and fourteen subtests
in 70.28 s. Both child formats, exact source/route/revision binding, rehashed
physical DXF corruption, forged process evidence, bounds and publication races
are checked. A regular installed wheel from `075fba5` plus the two frozen files
reproduces the orthogonal outer package outside the checkout: all fourteen
original facility files remain byte-identical, both child artifact checks PASS,
the pressure-cycle review stays FAIL, the package stays DRAFT and no solver is
called. Its twenty process pages were rendered and visually inspected.
Local proof is retained under `out/checkpoints/process-facility-package-i53/`.
Version `0.2` installation-path diagrams and complete Cycle I engineering
acceptance remain subsequent work.

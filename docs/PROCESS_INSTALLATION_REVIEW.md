# Supplied Installation Paths in a Process Revision

The explicit version `0.2` process input adds `installation_paths` to the
closed [version `0.1` review contract](PROCESS_REVIEW.md). Each entry is a
complete supplied [whole-equipment 2D path request](EQUIPMENT_INSTALLATION_PATHS.md).
The read-only extension recomputes local checks; it does not search for a path
or change the supplied layout.

```python
from layout_configurator.process_installation_review import (
    load_process_review_with_paths,
)

report = load_process_review_with_paths("process-with-paths.json")
```

The original UTF-8 file is the source authority. The loader does not remove
paths, rewrite the input as version `0.1`, or substitute a different building.
The nine original component identities remain shared by every schedule,
component report and path wrapper. The exact whole-file SHA additionally binds
the supplied path requests. The separate `installation_paths` section preserves
the requests, their set SHA, requested/covered/missing targets and fresh leaf
reports carrying each original request ID and SHA.

Raw component identities and the leaf's four canonical model signatures have
different contracts. Equipment is a raw array in the process input but an
object with derived directional clearance in the standalone model. Optional
layout fields and canonical contours also participate in model serialization.
The extension retains both representations; it does not replace leaf source
signatures with raw component hashes to make a failed binding pass.

Original equipment, card mapping, explicit installation targets, actions,
actual rooms and placements remain mandatory. Alternatives for one target may
have different request IDs; duplicate request IDs, cached reports and unrelated
targets reject. Missing requested paths remain UNKNOWN. A stale source or
known body collision remains FAIL even beside incomplete exterior evidence or
an independently failed pressure declaration.

Resource admission covers the original input and all requests before common
geometry or nested source decoding. Path-specific limits only tighten; they
bound individual and total poses, obstacles, geometry work and repeated source
node/byte work. Each fresh core and path check may decode the same structured
source again, so a new request cannot receive a fresh aggregate budget. Pure
endpoint checks reject positive clearances that collapse after world-coordinate
translation before any geometry is built. Budget and precision gaps remain
UNKNOWN with an explicitly unevaluated inventory.

Vertex admission includes actual orthogonal rooms, original environment and
library geometry, every supplied clearance/staging shape and the structured
source copies consumed by the leaf checker. A bounded lexical scan counts
point-array members without decoding source documents, including escaped JSON
keys. Text describing point arrays is not treated as geometry. Shared and
individual limits are checked before common geometry; source copies cannot
receive an independent vertex allowance.

Bounded delivery retains the common identity and originating path failure,
including request ID/SHA and the actual segment or obstacle identity. A
truncated inventory cannot become complete or erase a known FAIL.

The old `load_process_review` remains a closed version `0.1` reader. Its result
and the established documentation-limit fields remain unchanged. The old
document and outer-package APIs do not implicitly accept version `0.2`.

The process stays DRAFT with `.ok == False`, full facility/scenario and 3D
feasibility UNKNOWN, authenticated approval UNKNOWN and engineering acceptance
PENDING. A local 2D path PASS concerns only supplied supported motion and its
declared premises. Process enforcement in the producer, path diagrams,
engineering review and the Cycle I benchmark remain separate requirements.

Checkpoint I.5.4.1 passes fresh root single-file verification: 17 tests and
21 subtests in 21.08 seconds. Actual regressions exposed missing-library FAIL
and pre-common vertex admission gaps; the repaired cases preserve UNKNOWN,
known collisions and bounded failure witnesses. The unchanged core passes
21 tests/26 subtests and the standalone path passes 23 tests/21 subtests, each
in its own sequential invocation.

A regular installed wheel from `b0db8dd` plus the five frozen files reproduces
seven supplied cases outside the checkout. Clear, optional-field and embedded
inputs retain local 2D PASS; actual body/pressure conflicts retain FAIL;
missing library remains UNKNOWN without hiding a known body collision.
The original version `0.1` result SHA remains
`7c9bc8895fbc3c9907d960aa30b6e99c240054189d020edfd3fb6f11766d542a`,
all 48 historical document-limit fields remain, and the saved version `0.1`
PDF passes fresh QA. Exact source bytes remain unchanged and no solver runs.
A 4,096-byte cap retains the originating path request/segment/neighbor witness
in 3,310 bytes. [Compact evidence](acceptance/b0db8dd-process-paths.json)
records exact module/schema/test and wheel identities. Detailed local proof is
retained under `out/checkpoints/process-installation-review-i541/`.

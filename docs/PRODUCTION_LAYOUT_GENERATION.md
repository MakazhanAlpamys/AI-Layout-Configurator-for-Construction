# Finite production-layout candidates

Checkpoint I.4.3.3.1 is a standalone finite-catalog generation contract under
implementation. Producer verification, ordinary-wheel verification and final
checkpoint evidence remain pending. The verified fixture and missing-feature
results below do not establish that the new producer works.

The explicit API accepts an original production program, its original raw
BuildingIR frame and a mandatory finite catalog of room footprints:

```python
from layout_configurator.production_layout_generation import (
    generate_production_layout_candidates,
)

report = generate_production_layout_candidates(
    production_program,
    building_raw=building,
    room_catalog=room_catalog,
)
```

The catalog maps each original room ID to supplied choices. A rectangle uses
`x`, `y`, `width` and `height` in millimetres. An orthogonal contour uses
`kind="orthogonal"`, `units="mm"` and literal `vertices`. The contract does not
infer a catalog from a project brief. Keyword controls cover requested
`variants`, `seed`, `time_limit_seconds`, `deterministic_units`,
`include_windows` and `limits=ProductionLayoutGenerationLimits()`.

## Original source authority

Admitted output must retain detached original building, production-program and
catalog frames, with separate hashes for the whole building, top-level program,
catalog and program base building. The base-binding hash excludes only the
embedded `production_program`; it does not authorize removing that program
from the whole source frame. A supplied embedded program must exactly match
the top-level original, including integer/float distinctions and literal text.
Candidate IDs identify the original supplied descriptors, rather than a
normalized replacement contour.

Stale base binding and mismatched whole embedded data must stop before geometry
precision probes, model construction or solving. A valid original frame that
cannot retain its identity through the supported canonical roundtrip remains
UNKNOWN instead of being rewritten. Returned frames and evidence must be
detached from caller-owned objects.

The producer must preserve the project's required, forbidden and preferred
room relations. It must not delete, merge or rewrite hard relations to admit a
candidate. Requirements remain tied to their original requirement, operation,
source and room IDs, requirement/source hashes, reason, excerpt and basis.
Unknown usage rights or assumptions remain explicit provenance evidence.

## Admission before allocation

The separate generation limits extend
[production-layout review limits](PRODUCTION_LAYOUT_REVIEW.md) with finite
catalog, attempt and CP allocation controls and a bounded dimension policy.
Controls are closed, require actual supported types and can only tighten their
ceilings. Default counts will be documented after the source is frozen and
verified.

Joint admission must precede canonical constructors, GEOS and CP model creation.
It covers original JSON nodes, depth and complete serialized UTF-8 bytes,
including object keys, metadata and every introduced embedded-program
occurrence. Equal hashes do not remove repeated allocation work. Source text
bytes alone are not a complete snapshot budget.

The reservation also covers source and catalog vertices, room/catalog geometry
work, candidate-pair work, repeated dimension checks and fresh room/production
QA across the maximum admitted attempts. CP choice variables, possible preferred
support variables, constraints, objective terms and numeric coefficient bounds
must be admitted before their allocation. Conservative upper bounds can exceed
the eventual allocation after geometry rejects choices; these are work
reservations, not elapsed-time or operating-system memory measurements.

A bounded initial representative-layout production review may be needed to
retain an originating failure when the catalog kernel rejects a pair before
selection. Its work must be reserved separately. It cannot become an unbounded
Cartesian probe or establish acceptance for other combinations.

## Fresh candidate gates

Each delivered candidate requires fresh actual room QA and fresh strict
[production requirement QA](PRODUCTION_LAYOUT_REVIEW.md). Room checks include
source area/minima, boundary coverage, actual openings, door approaches,
topology and usable connectivity. Production required adjacency needs one
maximal contiguous shared span at least the canonical project's
`layout.door_width_mm`, without an epsilon allowance or disjoint-span summation.
Forbidden adjacency rejects a positive shared span; corners alone are legal.
Positive room overlap independently fails.

A real declared opening can be narrower than the project's production contact
threshold. Passing opening QA therefore does not replace strict production QA.
When a selected combination fails the fresh gate, a no-good constraint must
exclude that combination before a bounded retry. Rejection evidence must keep
the originating requirement and source identity, actual span and threshold.
A known failed supplied choice remains visible beside incomplete or UNKNOWN
evidence.

Complete locally checked requested candidates can yield `generation_status`
PASS. Unknown provenance still makes the overall report UNKNOWN. Empty
production requirements cannot support a positive production-generation claim.
Finite catalog exhaustion, an attempt/resource stop or an incomplete requested
variant set remains UNKNOWN and does not prove that the building is infeasible.
Source errors and stale bindings remain distinct from a rejected finite choice.

Scope stays DRAFT with `ok=False`, full facility/scenario validation and
three-dimensional feasibility UNKNOWN, engineering acceptance PENDING,
authenticated approval UNKNOWN and building infeasibility unproven. Equipment
packing, installation routes, process capacity, engineering calculations and
three complete accepted facility variants remain separate work.

Historical solver public signatures and defaults, automatic producer dispatch,
and the embedded-program facility/incremental capability barriers must remain
unchanged. This opt-in candidate API does not lift those barriers or approve an
external artifact.

## Verification state

Root's independent fixture admission passes one test/two subtests in 1.56
seconds. The connected three-room fixture checks real openings, door approaches,
topology and usable connectivity; its forbidden pair meets only at a corner.
Independent legacy finite-catalog epsilon fixtures pass in 0.87 seconds: the
first legacy-accepted choice has a strict 1,599.9999995 mm production contact
against a 1,600 mm threshold, while the alternative has 1,699.9999995 mm.
These fixtures establish a meaningful fresh post-selection gate.

The initial single-file feature RED records eight ordinary missing-module
failures, one fixture test passing and two subtests in 1.11 seconds. The joint
vertex regression now uses a ceiling of 15: the catalog's 12 vertices and
source boundary's four vertices each fit separately, while their combined 16
do not. Additional tests require preconstructor joint JSON/geometry and CP
allocation admission, raw identity retention, exact retry witnesses, finite
UNKNOWN scope and unchanged historical barriers.

Implementation and all post-implementation results are pending. Root will add
the compact evidence at
[acceptance/5bcfb2d-production-generation.json](acceptance/5bcfb2d-production-generation.json)
after verification. Independent local fixture records remain under
`out/checkpoints/production-layout-generation-i433/`; no ordinary-wheel,
changed full-CI or complete-facility acceptance claim is made here.

## Paused work snapshot — 2026-10-15

The user requested a safe stop. The first implementation is saved as WIP,
not a completed checkpoint. No producer GREEN, historical kernel regression,
paired-wheel build or complete acceptance has been run. The completed main
baseline is `5bcfb2d`, including the separately verified QA.26 repair.

Two review findings remain unresolved:

- The decoded snapshot must independently match the admitted original hashes
  before constructors. Rechecking only the caller's current frames cannot
  detect an A-to-B-to-A capture substitution.
- Failure delivery must carry the fresh leaf's total and omitted originating
  failure counts. A small finding cap must retain a source-bound requirement
  witness beside generic conflicts instead of reporting a false zero omission.

After an explicit resume, preserve the snapshot, restore its four owned files
onto the main baseline, prove these cases with focused regressions, repair the
guards, then run one test file at a time. The paired-wheel helper at
`tmp/verify_production_generation_wheel.py` is prepared but has not been run;
its old-source archive is pinned to `5bcfb2d`. Raw admission and RED evidence
remain in `out/checkpoints/production-layout-generation-i433/`.

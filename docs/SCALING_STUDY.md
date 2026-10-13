# Scaling: 13 → 20 → 30 → 40 rooms

## What was measured and on what

The goal is to find out where the reach of the current pipeline ends, without weakening
the checks. The profile, publication gates and number of variants are the same as for the pilot
(`rules/pharma_cleanroom_pilot.yaml`, `--variants 2`). Only the size of the
program was changed.

| Program | Rooms | Equipment | Boundary, mm | Fill ratio |
| --- | --- | --- | --- | --- |
| `examples/pharma_cleanroom_pilot.yaml` | 13 | 5 | 54 000 × 30 000 | 0.57 |
| `examples/scale_20_rooms.yaml` | 20 | 10 | 60 600 × 33 700 | 0.57 |
| `examples/scale_30_rooms.yaml` | 30 | 20 | 68 900 × 38 300 | 0.57 |
| `examples/scale_40_rooms.yaml` | 40 | 30 | 76 400 × 42 500 | 0.57 |

The pilot's process core (13 rooms, zones, airlocks, 7 flows, stages)
is kept unchanged; support rooms and equipment were added.
The boundary fill ratio was held at 0.57 so that the size of the problem changed, not
its density.

Environment: Windows 11, Python 3.11.15, OR-Tools 9.15.6755, one worker — the development
machine **before the migration of 2026-09-10**. All timings below refer to that machine;
the series was not rerun on the current machine. The conclusions about where exactly the pipeline
stops scaling do not depend on the machine; the absolute seconds do.

## Correction to the first version of the programs

The first version attached every support room to a single
`staff_corridor`. This makes the program infeasible by construction: a contact requires
`door_width + 2 × wall_thickness` = 1300 mm of shared boundary, so 27 rooms
would have requested about 35 m of frontage from a corridor with a full perimeter of 45 m. Verified:
such a 30-room program did not yield a single candidate even in 300 seconds.

The constraints were not weakened in the process. The program itself was fixed: circulation
scales together with the facility — for every 6 support rooms
a corridor segment is added, and the segments are linked into a chain. That is what a real
facility does, and that way a failure measures the solver, not invented geometry.

## Success rate

`--variants 2`, `--max-attempts 3`, `--time-limit 40`, seeds 1 / 7 / 42.

| Program | Succeeded | Run time | Why candidates were rejected |
| --- | --- | --- | --- |
| pilot 13 / 5 | **2 of 3** | 122–123 s | `FLOW_COMPLETENESS` |
| 20 / 10 | **1 of 3** | 42 s, 52 s and 3842 s | `FLOW_COMPLETENESS`, or no candidates at all |
| 30 / 20 | **0 of 3** | 41–55 s | no candidates from the room solver |
| 40 / 30 | **0 of 3** | 41–42 s | no candidates from the room solver |

The only successful run at 20 rooms took 64 minutes. The spread between
seeds is orders of magnitude, not a small multiple.

## Where the time goes

Stages, measured separately:

| Stage | Pilot, 13 rooms | 20 rooms |
| --- | --- | --- |
| Room solver | 41.5 s | 102.1 s |
| Equipment packing | 0.6 s | 1.8 s |
| Equipment validation | 0.0 s | 0.0 s |
| Flow routing | 0.0 s | 0.1 s |
| Flow validation | 0.0 s | 0.0 s |
| Facility policy | 0.1 s | 0.2 s |

The room solver is the only bottleneck. Everything else combined stays under
2 seconds even at 20 rooms. Model construction is not the cause either: 0.0 s on
the pilot and 0.1 s at 40 rooms.

### `--time-limit` is not a hard ceiling

The requested 40 seconds resulted in 41.5 s on the pilot and **102 s** at 20 rooms.
The overrun is not related to model construction; the limit is checked between CP-SAT
search stages, and an individual stage can go far beyond it. CI timeouts
must not be planned from `--time-limit` without a margin.

## Main conclusion: solution quality does not coincide with acceptance

The 20-room program, seed 1, the same input:

| Budget | Objective | Acceptance result |
| --- | --- | --- |
| 40 s | 80 195 154 | `FLOW_COMPLETENESS` FAIL |
| 120 s | 42 906 583 | **all checks PASS** |
| 300 s | 1 515 021 | `FLOW_COMPLETENESS` FAIL |

The larger budget gave a solution 28 times better by objective — and yet one that does not pass.
The reason is that the objective minimizes area deviation plus a positional
tie-breaker and knows nothing about flow routability. Therefore "giving the
solver more time" is not a scaling strategy: acceptance depends
on whether a particular candidate got lucky, not on search quality.

This also explains why the pipeline is structured as generating a pool of candidates with
independent filtering: selection is not a safety net but the main mechanism.

## The wall at 30 rooms

At 30 and 40 rooms the room solver does not find **a single** feasible solution within
300 seconds. This is a failure to find a first solution, not a failure of optimization.

This is not proof that the program is infeasible: the budget was exhausted, and an
`INFEASIBLE` status was not obtained. The claim is strictly this — with the current monolithic
CP-SAT formulation and a budget of up to 300 seconds, no feasible packing was found.

## Implications for the roadmap

1. Increasing the budget does not solve the problem: between 40 and 300 seconds acceptance does not
   improve monotonically, and at 30 rooms there is no solution at all.
2. The hierarchy from `PRODUCT_PLAN.md` (zones → rooms within a zone) is needed, not a single
   monolithic CP-SAT for the whole facility. Decomposition into linked subproblems is what
   changes the complexity class; right now all 30–40 rooms are packed by a single
   `AddNoOverlap2D`.
3. The objective should be brought closer to the gates. As long as routability does not take part in
   the objective function, the best candidate by area may be one that does not pass.
4. The stages after the room solver need no headroom: they are two orders of magnitude cheaper.

## Hierarchical room solver — 2026-09-27

The monolithic model was replaced as the default room stage for facility
generation by the hierarchical solver in `src/layout_configurator/hierarchy.py`
(`--room-solver auto`, which falls back to the monolithic model). Nothing in the
gates, the profile or the programs was changed.

### What the diagnosis showed first

On `scale_30_rooms.yaml`, first-solution search only:

| Variant of the monolithic model | Result |
| --- | --- |
| as is, 60 s, 1 or 8 workers | no solution |
| without required adjacencies | solution in about 16 s |
| minimum contact reduced to 1300 mm or even 100 mm | no solution |
| 500 mm or 1000 mm grid instead of 100 mm | no solution |
| area as a full size table or a 8–32 shape menu instead of `width × height` | no solution |

The contact tree (30 rooms, 29 required pairs, corridors with 8 contacts each)
is what the single `NoOverlap2D` cannot search; domain size, area
formulation and contact length are not.

### Method

Hubs (three or more required neighbours, usually corridors) are grouped with
their leaf rooms; daylight, external-entry and zone-relation rooms stay single.
Each cluster is solved as a small layout in a few box proportions, with the hub
touching as many box sides as it has outside neighbours. The top level places
only clusters (offset, shape, mirror, quarter turn); room coordinates are linear
in these choices, so every cross-cluster contact, daylight and entry constraint
is exact, and rooms rather than cluster boxes may not overlap.

### Same series, same settings

`--variants 2 --max-attempts 3 --time-limit 40`, seeds 1 / 7 / 42, Linux,
Python 3.11.15, OR-Tools 9.15.6755 (a different machine from the 2026-09-06
series, so absolute seconds are not comparable one to one; success rates are).

| Program | 2026-09-06, monolithic | 2026-09-27, `auto` | Solver used |
| --- | --- | --- | --- |
| pilot 13 / 5 | 2 of 3 | 3 of 3 (CI matrix, 3 variants, 9 attempts: 11 s for all seeds) | hierarchical |
| 20 / 10 | 1 of 3 (42 s, 52 s, 3842 s) | **3 of 3** — 5.8 s, 14.0 s, 29.3 s; 0 rejected candidates | hierarchical |
| 30 / 20 | 0 of 3 | 0 of 3 — about 85 s each | fallback to monolithic |
| 40 / 30 | 0 of 3 | 0 of 3 — about 87 s each | fallback to monolithic |

At 30 and 40 rooms the hierarchy fails at the top level. With a longer budget
and four workers the top level for 30 rooms was **proven infeasible** for the
cluster shapes it was given (145 s). That is a statement about the shapes, not
the program: corridor segments there declare `min_depth` 12 000 mm, and
minimum dimensions are per axis, so a corridor cluster cannot be turned and
its few box proportions do not interlock. When quarter turns were allowed
regardless (which violates those minimums), a top-level placement was found in
3.6 s. The next step is a richer shape search per cluster (more proportions,
hub position and side choices, or shapes generated on demand from top-level
conflicts), not a larger budget.

## 30 and 40 rooms solved — 2026-09-27 (later the same day)

The failure at 30/40 rooms turned out to be in the program data. Minimum
dimensions were bound to the x and y axes, so a support corridor declared "at
least 12 m" could only run vertically; three chained corridor clusters then
need about 36 m of the 38.3 m boundary height. Three changes, no gate relaxed:

1. **`rotatable` rooms** (`LayoutIR` room field, default `false`): the minimums
   may be met in either orientation. In the scale programs only the
   `support_corridor_*` segments are marked `rotatable: true`.
2. **Free-size single rooms at the top level.** Only multi-room clusters keep
   fixed shapes; with fixed single-room shapes the 40-room top level found
   nothing.
3. **Parallel top-level search** (up to 4 workers) with a wall-clock budget
   only; `--deterministic-budget` remains single-worker and repeatable.

Same series, same settings (`--variants 2 --max-attempts 3 --time-limit 40`,
seeds 1 / 7 / 42, 4-core Linux machine):

| Program | 2026-09-06 monolithic | 2026-09-27 morning | 2026-09-27 now |
| --- | --- | --- | --- |
| pilot 13 / 5 (CI matrix) | 2 of 3 | 3 of 3, 11 s | 3 of 3, **6.9 s** for the whole matrix |
| 20 / 10 | 1 of 3 (up to 64 min) | 3 of 3, 6–29 s | **3 of 3**, 7.4–8.0 s |
| 30 / 20 | 0 of 3 | 0 of 3 | **3 of 3**, 11.7–14.6 s |
| 40 / 30 | 0 of 3 | 0 of 3 | **3 of 3** on an idle machine: 65.8 s, 31 s, 39 s |

Every accepted candidate passed all publication gates (layout, equipment,
flows, profile, bundle QA).

Caveat, measured rather than assumed: in the first pass of the series the
40-room seeds 7 and 42 ran while the 164-test suite was using the same four
cores, and both fell back to the monolithic model and failed (109 s, 93 s).
Re-run on an idle machine they passed. A wall-clock budget with a parallel
search depends on the CPU it gets; for the 40-room program, 40 s is close to
the edge. CI timeouts for large programs need headroom, or the
deterministic budget.

## Reproduction

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\scale_20_rooms.yaml --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\scale-20 --variants 2 --max-attempts 3 --time-limit 40 --seed 42
```

Machine-readable results of the series are saved to
`out/scale-study/scale-report.json`: settings, timings, accepted candidates and
grouped rejection reasons for each run.

## Invocation-local cluster reuse — 2026-10-12

Direct `solve_layouts_hierarchical(..., cache_cluster_shapes=True, evidence={})`
and `solve_layouts_auto(..., cache_cluster_shapes=True)` can reuse ordered
equivalent local cluster shapes within one invocation. The cache is opt-in and
never persists across jobs. Default and explicit False preserve legacy results
and evidence. Facility/CLI dispatch does not enable it automatically.

Keys include actual room attributes/internal relations, equipment-fit groups,
external side requirements, inherited local settings, boundary bounds, motif
search policy, seed and budgets. Source IDs are remapped by ordered room roles.
Immutable coordinates are independently checked for grid, area/dimensions,
contacts, local connectivity and equipment fit before every hit. Empty bounded
searches are never cached as infeasibility proofs. Evidence records misses,
validated hits, rejected entries and actual global CP-SAT status with a scope of
`selected_cluster_shapes`.

The synthetic 80-room probe duplicates the unchanged 40-room program into
qualified A/B identities and adds one hard receiving-room bridge. On the busy
Windows development machine, seed 1 and a 2 s per-model budget measured 52.669 s
without reuse and 27.125 s with six misses/six independently validated hits.
Both global searches returned UNKNOWN and no room candidate. A 10 s per-model
cached probe also returned UNKNOWN in 36.078 s. These are optimization diagnostics,
not accepted facility timings; equipment/routes/full bundle QA did not run.

Different equipment requirements correctly prevent reuse even for similarly
named support clusters. NEXT 1.5 still requires an actual accepted 80-room program
under 120 s on pinned CI hardware, a third hierarchy level and incremental
re-solving. That criterion remains open.

## Soft hierarchy warm-starts — 2026-10-12

The direct hierarchy and automatic dispatcher accept `hint_rects` as optional
room-to-rectangle search guidance. Partial or stale cluster hints do not remove
ordinary patterns. A complete source cluster may add one independently checked
shape with at most eight legal orientations; source dimensions, adjacency,
grid and equipment-fit requirements are rechecked before supplementation.
Positions remain soft, and hard constraints and preferences retain priority.

A bounded cloned-model stage completes auxiliary hint variables with supplied
values fixed only in that clone. Its budget is at most one second and .05 work
units when deterministic units are supplied. Successful independently checked
coordinates become complete soft hints in the unrestricted original global
model. Failed completion returns ordinary repair search and cannot prove source
infeasibility. Evidence retains completion/global statuses and actual timings.
Hint repair uses one worker: the current OR-Tools parallel repair portfolio
aborted inside its fixed-search heuristic during the real small counterexample.
Ordinary no-hint hierarchy searches retain their existing worker policy.

Repeated source-room resizing passes complete room/equipment/flow/profile gates.
Nine no-hint/empty/disabled-cache result and evidence comparisons with `501065e`
are byte-exact. Fresh root checks: 87 hierarchy/hint/cache/constraint tests with
67 subtests, plus 29 editor/facility-editor/UI tests.

The exact synthetic A/B clone of a supplied pinned accepted 40-room layout,
with one hard receiving-room bridge, reached 80 rooms locally in 48.019 s:
.584 s source validation/derivation, 20.595 s room search including .591 s cloned
completion, and 26.837 s fresh equipment, routes and independent pharma gates.
Final **unrestricted** global CP-SAT returned FEASIBLE with 1,207 complete soft
hint values. Source-40 generation from scratch was excluded; its readback/check
and derivation were included. This measurement excludes full persisted export
and bundle QA. It is a scoped warm-start result, not general 80-room generation
or CI acceptance. The unchanged complete persisted CI gate remains to measure.

## Complete supervised warm-start bundle — 2026-10-12

The manual `warmstart80.yml` workflow measures one installed-package Ubuntu
request. It is separate from quick checkpoint CI and the existing full scale
matrix. The source fixture pins raw40, full profile, the exact accepted
`35eff27` seed-7 variant-2 bytes and the derived80 program. Every source room,
zone, equipment and flow attribute is copied into qualified A/B IDs; one hard
receiving bridge is added. Source40 is independently rechecked before its
geometry supplies soft hints. Original40 generation from scratch is excluded.

A native parent supervises the complete worker under a 120-second ceiling,
including startup/imports, source validation/derivation, unrestricted global
room search, fresh equipment/routes/pharma checks, shared full bundle export,
independent persisted DXF/PDF/thematic/IFC/BCF/manifest/client-report QA and
strict IFC EXPRESS. Timeout retains negative evidence and cannot publish
acceptance. Runtime/dependency/executable/resource/fixture fingerprints are
rechecked; CI rejects editable/check-out package imports. Workflow concurrency
cancels duplicate requests without cancelling independent full acceptance.

The one measured Windows request passed in **64.735 s**: preparation including
imports 1.235 s, rooms 20.766 s, equipment/routes/pharma 27.281 s, complete export
3.062 s, persisted QA 2.609 s and strict EXPRESS 8.875 s. The remaining time is
supervision, fingerprints and reporting. Its [raw report](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/blob/77a6b88628c6f3c4fcdff494ab398bf2b0d0ac1d/research/scale80/2026_10_12/windows_warmstart.json)
identifies actual `a899ac1` plus the then-working client-report implementation
and its earlier runner fingerprint. Later launch/provenance/resource refinements
and the final report continuation changes are covered by focused tests and the
separate pinned CI measurement below; this raw local timing is not relabelled
as a clean latest-commit result.

The installed-wheel Ubuntu [run 38291669488](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38291669488)
at clean `ac0408b42a5ccbf6b6a895cf568832ab529d93c3` passed the complete request in
**33.008 s**. Preparation including imports took 1.193 s, room search 10.742 s,
equipment/routes/profile gates 12.051 s, complete export 1.875 s, persisted QA
1.311 s and strict EXPRESS 5.025 s. All nine saved-artifact checks passed:
80 IFC spaces, 60 equipment objects, 16 routes, nine thematic pages and fourteen
client-report pages. The unrestricted global model returned FEASIBLE; six
cache hits were independently checked. No source positions became constraints.
All four supplied-source byte hashes and nine exported-artifact hashes were
verified after download. The report confirms regular installed package imports
and all 22 authoritative resources resolved from its installed `_data` tree.
Its unchanged runtime fingerprint is
`1f7867569f92b284733fd4f5b86f013e8869657522da01cc9d116e30275b999c`.
The [raw Linux report](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/blob/77a6b88628c6f3c4fcdff494ab398bf2b0d0ac1d/research/scale80/2026_10_12/linux_warmstart.json)
retains the exact scope, source derivation, dependency versions and timings.

Run explicitly, or dispatch the manual workflow at a pinned commit:

```powershell
python scripts/scale80_warmstart_acceptance.py --output out/scale80-warmstart --max-seconds 120
```

This measures the supplied synthetic warm-start scenario only.
General scratch80 solving, a third physical zone level and measured large edit
acceptance remain open parts of NEXT 1.5.

## Canonical program edits with soft prior geometry — 2026-10-12

`facility_incremental.re_solve_facility_after_edit` accepts the prior canonical
BuildingIR, prior room result, edited BuildingIR and current profile. This first
slice requires unchanged room, zone, equipment and flow IDs. It hashes all
inputs and records changed/unchanged IDs without mutating the supplied objects.
Prior room geometry, grid and hard zone relations are independently checked;
that geometry-only input does not establish prior equipment/profile acceptance.
Hints incompatible with an edited grid are skipped with room/reason evidence.

Old coordinates remain soft guidance. No fixed rectangles are synthesized.
The ordinary room backend retains unrestricted final search and invocation-local
shape reuse; stale room sizes and equipment requirements invalidate local shapes.
Every returned current candidate passes fresh room, equipment, route and profile
gates. Rejected inputs and bounded generation failures retain source provenance
and cannot establish whole-program infeasibility. At the initial H.3.5 checkpoint,
physical zone strategies rejected the then-unimplemented hint/cache bridge
before building a model.

Actual room-area and equipment-width edits on a six-room, two-cluster source
repair stale geometry and pass every current gate. Root verification covered
54 tests and 49 subtests across incremental requests, stage evidence, hints,
cache and both zone dispatch strategies. Empty/disabled options preserve default
generation output. The CLI/editor/API bridges and recursive guidance are
recorded below; measured 80-room edits remain separate follow-up work.

The CLI exposes this path explicitly:

```powershell
python -m layout_configurator.cli generate-building edited.json --incremental-from prior/building_01.json --output out/edited --room-solver hierarchical --variants 1
```

The supplied result may contain room geometry only; prior full-facility acceptance
is not inferred from it. Its exact file SHA256 is retained alongside canonical
source/edit/profile hashes in both the manifest and saved variant. Actual current
gates and the shared complete bundle writer run afresh. Changed IDs or malformed
sources produce structured input-rejection evidence; unresolved current profile
conditions preserve incremental provenance and stage evidence. Imported fixed-room
constraints cannot be combined with this soft mode. Source and edited-input files
must be outside the output directory to avoid overwriting supplied evidence.
Ordinary no-option CLI behavior retains its existing path.

`FacilityEditSession` also offers explicit `edit_room_solver` and
`edit_cache_cluster_shapes` settings. Its ordinary default remains monolithic.
An opted repair fixes only the room position/dimensions the user requested;
neighbor coordinates are soft hints. Current equipment, routes and profile
checks remain in the editor flow. Solver evidence belongs to the corresponding
state: failed repair leaves it unchanged, undo/redo/reset restore it, a direct
geometry-valid edit clears stale evidence, and saved/reopened revisions retain
it. Snapshot consumers receive a deep copy. This session hook is separate from
large-scale edit acceptance.

Root verification covered 54 editor/core/UI/preference tests and 27 subtests.
The actual seven-room repair fixes only the selected link and moves both
neighbor clusters, with one checked cache hit and one miss. Saved and reopened
revisions pass independent complete bundle QA. Default initial/repaired snapshots
were byte-exact against `9f1a365`; default saved generation/geometry and manifest
keys also match.

The actual editor launch now exposes both options explicitly:

```powershell
python -m layout_configurator.cli ui out/source --edit --edit-room-solver hierarchical --edit-cache-cluster-shapes
```

These options require a canonical facility editing session. Read-only launches
and plain LayoutIR editors reject them before opening a server. Omitted options
retain the ordinary launch arguments and session defaults. A real local HTTP
move/save exercise preserves repair evidence and passes independent revision
bundle QA. Fresh root launch verification: six tests and four subtests passed
in 8.88 s; the actual installed CLI help lists the supported controls.

## Physical zone warm-start and incremental bridge

Both explicit physical zone strategies now accept soft room hints and
invocation-local reuse of independently checked room-cluster shapes. Recursive
leaves share one cache across their ordinary motif solves. Subtree and global
models retain actual canonical containers, ancestor containment, sibling
exclusion, hard room/zone relations, fixed rooms, axes, cutouts and opening rules.
Old room bounding boxes never establish accepted physical zone geometry.

Opted guidance retains every ordinary size/pattern and may propose one additional
automatic grid size per unit. Explicit source size options are preserved. Up to
eight independently checked source/solved leaf orientations and four corner
translations preserve legal external contacts in padded containers. These are
finite candidates, not coordinate pins. Clone completion is bounded to at most
one second and 0.05 deterministic units; its physical geometry is independently
checked before values become soft hints in the untouched parent/global model.

The actual eight-room fixture has two four-room corridor clusters inside
canonical child zones and a parent zone, with equipment and a cross-zone route.
Cache reuse, a room-area edit and an equipment-width edit pass all current room,
equipment, route, profile and physical-zone gates. Conflicting prior hints cannot
override fixed coordinates, axes or cutout/entry/window/daylight constraints.
Bounded failure preserves actual CP status and cannot prove whole-program
infeasibility. Disabled/empty options retain default geometry and evidence;
separate old/new diagnostics against `4e2048d` match both ordinary zone modes.

Fresh root verification: 68 tests and 108 subtests passed in 17.80 s across zone
guidance, incremental repair, independent constraints, hints, cache and dispatch.
Malformed and excessive hint coordinates retain structured input failure before
projection or model construction.

This proves the small physical zones-to-clusters-to-rooms bridge. The unchanged
supplied 80-room benchmark, scratch generation and large edit acceptance still
require their own supervised measurements.

## Supervised recursive acceptance option

The pinned runner now accepts `--room-solver zone_recursive`; its default remains
`hierarchical`. Recursive mode consumes actual solver containers, independently
checks containment, exclusion and canonical zone relations before equipment or
exports, and records a separate `zones80` hash and geometry artifact. Old room
bounding boxes cannot establish a physical-zone PASS.

The parent deadline still covers startup, preparation, rooms, physical zones,
equipment, routes, profile checks, complete persisted bundle QA and strict IFC
EXPRESS. Structured bounded local/global failures retain their actual CP status
and scope. The pinned source and original warm-start report behavior are unchanged.
The manual workflow remains one installed-wheel Ubuntu job with an explicit
backend choice; no heavy job is added to push CI.

```powershell
.venv/Scripts/python.exe scripts/scale80_warmstart_acceptance.py --fixture tests/fixtures/warmstart80 --output out/scale80-zone-recursive --max-seconds 120 --room-solver zone_recursive
```

Use a fresh output directory and hold executable sources unchanged during the
measurement. Fresh root runner verification passed 16 tests and 27 subtests in
22.46 s; actionlint passed. The actual eight-room complete recursive bundle,
forged container, bounded CP failure and supervised deadline are covered. This
checkpoint prepares the unchanged physical80 measurement; it does not claim an
80-room performance result, general scratch generation or large edit acceptance.

### First unchanged physical80 measurement

One manual installed-wheel Ubuntu run at pinned `350ecee` completed in
62.009 s, including 60.130 s of room solving. Original supplied-source layout,
equipment, flow and profile gates all passed; all four fixture byte hashes are
unchanged. Local shape stages completed, but the global CP model returned
UNKNOWN for the selected canonical zone shapes. The runner published no accepted
facility bundle and retained structured `global/no_global_placement` evidence.
This is bounded search failure, without whole-building infeasibility proof.

[Run 38386146920](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38386146920)
retains the full artifact; [compact evidence](acceptance/350ecee.json) records the
exact commit, runtime fingerprint, source hashes, timing, 101 stage attempts and
raw-report SHA256. Raw report copies stay under ignored local research/output.
The earlier 33.008 s hierarchical warm-start acceptance remains a separate
measured result. Physical80, scratch80 and large edit acceptance remain open.

### Explicit final global search controls

`solve_zone_layouts` accepts optional `global_time_limit_seconds` in (0,120] and
`global_workers` in 1..8. These affect only each final global-model attempt;
local shape and recursive subtree budgets retain their existing settings.
Deterministic mode always uses one worker and its existing per-model work
allowance. Omitted controls preserve the previous output and evidence.

The supervised runner exposes `--zone-global-seconds` and
`--zone-global-workers` only with `--room-solver zone_recursive`. Input is
validated before starting a worker. The entire request still has its 120-second
deadline, source checks, actual physical-container QA and complete export gates.
The same one-job manual Ubuntu workflow offers 2/5/10 seconds and 1/2/4 workers;
checkpoint pushes acquire no heavy measurement job.

Fresh root runner/core verification passed 23 tests and 42 subtests in 26.12 s;
actionlint passed. The real small physical-zone bundle confirms final-only
parallel controls. Invalid/inapplicable settings start no worker, a native
timeout preserves the total deadline and ordinary serialized reports/arguments
remain unchanged. These tests establish the controls; they do not establish an
80-room runtime or acceptance result.

### Changed-controls physical80 measurement

One installed-wheel Ubuntu run at pinned `1813e19` used 10 seconds and four
workers for the final global CP model. It completed in 69.776 s, including
68.117 s of room solving. All original supplied-source gates passed and the
four raw fixture hashes remained unchanged. All local shape stages completed,
but the final model again returned UNKNOWN for the selected shapes; no complete
bundle was accepted. Longer final-model settings alone did not establish
physical80 acceptance. The result does not prove whole-building infeasibility.

[Run 38389399324](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38389399324)
retains the actual artifact; [compact evidence](acceptance/1813e19.json) binds
the source, installed runtime, requested/effective global settings, 101 stage
attempts and raw report hash. Raw research copies stay local. Further work must
address a measured search limitation rather than repeat the unchanged request.

### Explicit final-global diagnostic capture

`solve_zone_layouts(..., global_diagnostics=True)` adds source-bound metadata
about the effective final finite shape family, including exact integer-grid
room/container rectangles, ordered shape identities/hashes and fixed/axis
constraints. Actual CP proto variable/constraint counts are recorded before each
Solve, so added enumeration no-goods are included. Actual ResponseProto status,
timing, branches, conflicts, propagations, objective/bound and solution presence
describe that model attempt. Unavailable/nonfinite response values become null
with explicit field names.

Capture is bounded to 80 source rooms, 4096 candidate shapes, 65536 rectangle
records and 8 MiB of canonical family JSON. Exceeding a bound gives INCOMPLETE
with counts/reason and no partial geometry snapshot. COMPLETE means diagnostic
capture is complete; it does not mean the source program is feasible, infeasible
or accepted. Omitted/false options preserve earlier reports and solver behavior.

The supervised runner exposes `--zone-global-diagnostics` with the explicit
recursive backend. Its existing one-job manual workflow has a default-false
boolean input. No automatic heavy workload or deadline is added. The unchanged
120-second total budget includes diagnostic work and every original acceptance
gate remains in force.

Fresh recursive/runner regression passed 56 tests with 68 subtests in 45.36 s;
independent root checks passed four actual model/UNKNOWN/complete persisted
pipeline/deadline cases in 7.14 s. The accepted eight-room fixture retains native
EXPRESS and complete bundle QA. Pre-checkpoint root/recursive default output
comparisons and actionlint passed. No 80-room measurement is claimed for this
diagnostic implementation; the two recorded physical80 failures remain open.

### Bounded final cross-root contact pruning

`solve_zone_layouts(..., prune_cross_root_contacts=True)` precomputes necessary
contact compatibility for the selected final root shapes. It uses the same
rounded integer rectangles as the existing CP model. For contained positive
rectangles in nonoverlapping root envelopes, positive required room contacts
must use a common external side. Four exact side/translation interval
intersections determine whether all explicit cross-root room contacts can hold
at one relative placement. An empty intersection adds a redundant pairwise
Boolean clause; all original hard constraints and candidate geometry remain.
Existential zone relation groups are not interpreted as all-pairs contacts.

The whole pass is preflighted at 80 source rooms, 64 eligible root pairs,
65536 candidate pairs and 1048576 orientation/contact checks. Any exceeded bound
skips the entire pass before posting clauses. A pair whose rounded containment
cannot be established is conservatively skipped. Evidence distinguishes
examined, skipped and rejected pairs from the actual number of posted clauses.
It describes only the selected finite family and never proves the original
building program infeasible. Omitted/false controls preserve default JSON.

The supervised runner exposes `--zone-contact-pruning` with the explicit
recursive backend; the existing one-job manual workflow has a default-false
boolean input. Local/subtree budgets, the 120-second total deadline, all source
gates, physical-container QA, complete persisted export and EXPRESS remain.

Fresh agent regression passed 72 tests with 190 subtests in 51.76 s; root passed
five actual CP/finite-failure/limit-skip/complete-bundle/deadline cases in 3.68 s.
Independent tiny-grid geometry matched the interval kernel across 103 shape
families. Actual model telemetry confirms unchanged variables and family hashes,
with exactly one extra Boolean clause per rejected candidate pair. Pre-checkpoint
root/recursive default comparisons and actionlint passed. This implementation
does not establish an 80-room improvement; physical80 acceptance remains open.

### Source-pinned physical80 diagnostic measurement

One changed manual Ubuntu run pinned to `9e5e598` enabled diagnostics and contact
pruning with the same seed, original source and 10-second/four-worker final
settings. It took 61.146 s total, including 59.843 s room solving. The final
model returned UNKNOWN without a solution; no equipment, routing, export or
persisted 80-room QA was reached. Its ResponseProto objective is not an accepted
incumbent. Failure of the separate fixed-hint clone also proves nothing about
the unfixed source program.

Capture is COMPLETE: ten roots, 220 candidate shapes, 956 rectangle records and
65,720 canonical family bytes. Every individual shape hash and the family SHA
were independently recomputed. Actual pruning examined 3,552 pairs across nine
root pairs and posted 2,776 redundant Boolean clauses, with no unsafe-containment
skips. The actual final CP model contains 1,651 variables and 7,934 constraints;
its 10.004-second search records 85,255 branches and 15,501 conflicts.

All four supplied raw files, prepared program, hints and manifest are byte-equal
to both prior measurements, and all eleven stage hashes match. The artifact ZIP
SHA and all eight extracted files were checked against their actual bytes.
[Run 38398872773](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38398872773)
and [compact evidence](acceptance/9e5e598.json) retain the immutable source pin,
installed noneditable runtime, complete model metrics and explicit negative
scope. Raw candidate geometry stays local and in the bounded CI artifact.

Pair compatibility leaves a finite relative-translation family; pruning alone
did not make the original CP formulation find a solution. The next implementation
can couple shape choices to those exact translation intervals while retaining
all original constraints and source gates. Repeating this unchanged request
would not establish the remaining physical80 criterion. Durations across these
single runs are observations under differing solver controls and runner load,
without a general performance guarantee.

### Bounded final shape/translation support

`solve_zone_layouts(..., cross_root_contact_support=True)` adds exact necessary
translation supports to the final root model. Each participating root's existing
one-hot choice defines one candidate-index variable. Each eligible required
cross-root pair has a six-column allowed-assignment table: two candidate indices
and the lower/upper bounds for relative X and Y. Four linear constraints bind
the actual root translation to that row. The four-side interval kernel uses
the same effective integer-grid rectangles as the original CP model; no grid
points are enumerated. All original hard clauses, geometry and objective remain.

If any candidate in a root pair has unsafe rounded containment, that entire
pair is skipped, including its otherwise safe candidates. Empty safe support
is passed to CP-SAT as an empty table; a resulting INFEASIBLE status applies
only to the selected finite shape family. It does not prove the source program
infeasible. Optional pruning and support can run independently or together.

The whole pass checks the existing 80-room/64-pair/65,536-candidate-pair/
1,048,576-orientation limits and additionally 65,536 participating rectangle
records and a conservative 16,384-row bound. Any exceeded budget skips the
entire pass before posting constraints. Evidence records actual rows/cells,
skipped pairs and extra model variables/linears/tables. Omitted and false
controls preserve earlier serialized results, evidence and runner arguments.

The supervised recursive runner exposes `--zone-contact-support`; the existing
single-job manual workflow adds a default-false boolean input. Final-only
controls retain the unchanged total deadline and all original physical/source,
equipment, routing, saved-bundle and native EXPRESS acceptance gates.

Final focused agent tests passed 18 cases / 36 subtests in 6.85 s; related
regression passed 87 / 272 in 30.88 s. Root independently passed six actual
geometry/model/unsafe/limit/selected-family/complete-bundle cases in 5.47 s.
Actionlint and pinned-default comparisons passed. A helper-only reconstruction
of the saved80 family gives 792 support rows and 4,752 cells, with potential
raw-model additions of 46 variables, 46 linears and nine tables. This analysis
does not run the 80-room solver or establish performance; the next changed
source-pinned measurement remains a separate gate.

### Full-CI partition and independent acceptance jobs

Checkpoint pushes still run compilation, complete pytest collection and the
existing source/geometry contracts. Full platform/container/pharma acceptance
remains on manual, pull-request and weekly events. Documentation-only pushes
are skipped, and cheap pushes cannot cancel a pinned full run.

The first `ci-test-shards/0.1` implementation used two shards per operating
system. Every invocation
performs complete `unittest discover` once and rejects loader errors, duplicate
IDs, empty required shards or a nonexhaustive/disjoint partition before running
anything. The draft-bundles shard contains exactly modules whose basename starts
with `test_orthogonal_bundle`, including future modules; core contains everything
else. Relative discovery order, all test assertions, skips, expected failures
and nonzero failing outcomes remain. Unique OS/shard artifacts retain exact
partition inventory, elapsed time and bounded outcome details for 14 days.

At freeze, actual dry collection found 1,113 unique IDs: 1,060 core and 53 draft
bundle cases, with zero import errors and no test execution. Eight real tiny-root
runner tests and nine subtests passed in 2.60 s; root independently passed three
actual execution/import-failure/duplicate guards in 0.91 s. Actionlint passed.
The unused pharma dependency on the entire platform matrix was removed because
that job independently builds and checks its own nine variants and consumes no
test artifacts. Its original seeds, budgets and every saved-file gate remain;
installed-container API acceptance is unchanged.

Four test VMs replace two and repeat dependency setup/discovery. The aim is to
shorten the critical path, not claim lower aggregate runner use. Any elapsed
benefit requires actual pinned remote timing. Earlier full acceptance at
`7561213` remains separate evidence for its own source and 1,001-test workload.

### Actual translation-support physical80 measurement

The single changed manual run at pinned `93fdc7c` enabled both pruning and
translation support with diagnostics, seed 7 and the same 10-second/four-worker
final limit. It took 43.515 s overall and 42.313 s room solving. Actual final
CP returned INFEASIBLE in 0.031 s with no solution. Its proof applies only to
the selected finite shape family. Source program infeasibility remains false;
equipment, routing, exports and complete persisted80 QA were not reached.

Capture is COMPLETE with ten roots, 220 shapes, 956 rectangle records and
65,720 canonical family bytes. Actual support has 816 rows / 4,896 cells,
ten candidate-index and 36 endpoint variables, 46 linears and nine tables.
Pruning adds 2,760 clauses; no unsafe root pair is skipped. The actual final
model contains 1,697 variables and 7,973 constraints. These metrics describe
this actual family, rather than substituting the earlier 792-row helper estimate.

Every original supplied source file, prepared program, hints and manifest is
byte-equal to all three earlier physical80 measurements; all eleven stage hashes
match. The current family differs at twelve ordered shapes in three roots despite
equal per-root candidate counts. Local wall-clock search and runner conditions
therefore prevent a causal cross-run performance claim. Every shape/family hash,
the artifact ZIP digest and all eight extracted file bytes were checked afresh.
[Run 38404321208](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38404321208)
and [compact evidence](acceptance/93fdc7c.json) retain the complete negative scope
and installed noneditable runtime. Raw family geometry remains local and in the
CI artifact. The next source-preserving work must address actual candidate-family
limitations; repeating the same selected family is not an acceptance strategy.

### Selective legal automatic-leaf turns

H.3.6.10 adds explicit `selective_leaf_turns=False` and the supervised runner
flag `--zone-selective-leaf-turns`. An automatic root leaf qualifies only when
one original interface room serves at least two distinct neighboring units.
At most four deduplicated transposed/reflected choices append after every old
choice. Original per-axis minima, areas, internal contacts and source group
constraints are checked against the actual swapped envelope. Explicit size
pools, canonical parents, fixed/axis-bound units and units with supplied
openings or equipment are conservatively excluded. Canonical source geometry,
axes, openings, IDs and existing candidate order remain intact.

The saved 93fdc7c family demonstrates the omitted pattern: a legal staff turn
needs an 11,000 by 11,100 mm envelope, while the original local 11,100 by
11,000 filter excluded it. A real small final CP counterexample changes from
INFEASIBLE to accepted geometry with the additional four choices; independent
original room/zone/daylight checks pass. The complete small supervised producer
also passes fresh persisted bundle QA and strict IFC EXPRESS.

On the independently reloaded saved physical80 family, pure geometry helpers
preserve original source/family hashes and all 220 ordered choices, then append
four choices for each A/staff_zone and B/staff_zone. The resulting 228-shape
family has 980 rectangle records and 67,548 bytes. Contact support remains
APPLIED with 912 rows and upper bound 14,592 within the unchanged 16,384 limit.
Every addition is rejected if the full proposed family exceeds existing bounds.
These helper counts establish candidate availability; no full80 solve ran here.

Agent regression passed 103 methods/218 subtests in 30.815 s. Fresh root passed
all 14 new methods/20 subtests in 4.63 s, independently reconstructed the exact
saved family and support counts, and checked all seven frozen source hashes.
Actual old/current omitted/False tiny result/evidence and default runner JSON
are byte-equal; actionlint passed. The next changed pinned manual80 measurement
must establish actual equipment, routing, exports and complete persisted gates.

### Actual selective-turn physical80 measurement

The single changed run at pinned `4604a10` enabled selective leaf turns alongside
diagnostics, pruning and translation support. The original seed 7, ten-second
four-worker final solve and 120-second parent limit were retained. Overall time
was 60.536 s, including 58.863 s room solving. Final CP returned INFEASIBLE in
0.049 s without a solution; it still describes only the selected finite family.
Equipment, routing, exports and complete persisted80 gates were not reached.

The COMPLETE actual capture contains ten roots, 228 shapes, 980 rectangle
records and 67,548 canonical bytes. Four appended choices are present in each
staff root. Actual support has 888 rows / 5,328 cells, within the 14,592-row
upper bound; pruning posts 2,776 clauses. The final model has 1,705 variables
and 7,989 constraints. These actual counts differ from the pure saved-family
helper estimate. Eight earlier ordered shape hashes also differ in B/logistics
and B/staff_zone, so cross-run times do not isolate the turns' causal effect.

Root independently checked every shape/family hash, the archive SHA256 and all
eight extracted file bytes. All four supplied files, prepared source, hints and
manifest are byte-equal to the four prior physical80 runs; all eleven stage
hashes match. [Run 38412433987](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38412433987)
and [compact evidence](acceptance/4604a10.json) preserve the negative result.
Physical80, general scratch80 and large edits remain open. The next change must
address another actual family limitation or a certified conflicting relation;
this unchanged failed run should not be repeated.

### Third complete CI shard

QA.20 changes the runner evidence to `ci-test-shards/0.2` and runs three complete
module groups per OS: `core`, `draft-bundles` and `exchange-and-cli`. The latter
contains modules starting with `test_orthogonal_cli`, `test_facility_floors`,
`test_packaging`, `test_domain_pilots` or `test_plan_import_cli`. The exact draft
prefix remains; all other present and future modules stay in core. Entire
modules and their class fixtures remain together and keep discovery order.

Root independently verified complete actual discovery and retained every one
of the prior 1,113 pinned CI IDs. At this checkpoint the tracked tests plus
the frozen CI changes contain 1,183 IDs: 1,052 core, 53 draft bundles and 78
exchange/CLI. Separate in-progress tests are excluded from that checkpoint
count. Actual fixture runs cover all three groups, future prefixes, class-cache
reuse, loader errors, duplicates across noncore groups, any missing group,
failed subtests and nonzero outcomes: ten root tests passed in 3.019 s.

The workflow changes only the shard matrix. API/container, nine pharma
variants, quick-push contracts, all acceptance assertions, fail-fast=false,
15-minute limits and unique always-upload artifacts remain unchanged. Six test
VMs replace four, adding two setup/discovery costs. Historical log attribution
suggests about 302.7 s of Windows core and 238.1 s of Linux core move into the
new group; these estimates precede the pilot fixture optimization and do not
promise new elapsed time or lower aggregate runner minutes. QA.21 subsequently measures one complete milestone at `70ee96e`: 1,202 cases
per OS and all active gates PASS, workflow416 s versus prior792 s, slowest
Windows exchange job411 s. Eight archive digests and all86 extracted files
match. Changed fixtures/source, 89 additional tests and six VMs prevent causal
attribution or guaranteed aggregate runner savings;
[evidence](acceptance/70ee96e.json) retains actual individual test/job times.

### Source-derived three-room leaf repacking

H.3.6.12 follows a pure check of the exact failed `4604a10` family. Within each
half, original two-sided staff contacts and office daylight restrict the staff,
clean, support and logistics choices: all48 surviving four-root combinations
force logistics outside the boundary. This is a finite-family geometric check,
not a deletion-minimal core or original-building infeasibility proof. An original
40-room A component with a narrow repacked staff leaf passes its source room,
physical-container and daylight predicates; B/bridge/equipment/routes remain
outside that component check.

The opted policy accepts only an automatic canonical ROOT leaf with exactly
three rooms, one interface room serving at least two distinct external units,
and both required internal interface/end-room contacts. Explicit pools, parent
containers, equipment, explicit openings, fixed rooms and selected axes remain
excluded. It derives a shared original-axis width from grid-rounded source
minima/contact width, each depth interval from source minima and min/max area,
a minimum daylight-end depth, maximum interface depth and target-area opposite
end depth. At most two Y-reflected stacks are freshly checked and appended after
all existing choices. This explicitly extends the automatic aspect heuristic
only in this narrow opt-in; it does not transpose source minima or loosen any
hard predicate. Every whole-family shape/rectangle/pair/contact/support limit
preflights the additions together; overflow retains the original family.

`solve_zone_layouts(selective_leaf_repacking=True)` enables the policy; omitted
and False preserve result/evidence bytes and actual original model protos.
The supervised runner uses `--zone-selective-leaf-repacking`; the single manual
Ubuntu workflow has a default-false boolean, with the unchanged120-second total
request ceiling. Ordinary/default facility generation does not enable it.

Meaningful RED covers missing controls and malformed original scalars. The
agent's final159 related tests pass47.585 s; fresh root14 tests pass4.850 s and
actionlint passes. The actual tiny ten-room/four-root final model remains
INFEASIBLE with old quarter turns, then accepts the source-derived4000x32800
stack/reflection with all original constraints and fresh downstream persisted
QA; strict IFC EXPRESS has zero issues. Original actual three model protos,
results and evidence match pinned `1e0efad` for omitted/False controls.

Root independently reconstructs and hashes the exact prior80 capture, retains
all228 old shape identities/order, and recomputes four appended choices:
232 shapes,992 rectangle records,68460 canonical bytes, family SHA256
`60407ac7a5dd88106860b6515c206093c4882fcde42948f2671edd19a0d7df61`.
Fresh support has936 rows/5616 cells, upper bound14784<=16384. Original source
identity is unchanged. This helper check performs no80-room CP; fresh saved
small-bundle QA also passes. Full80 placement, bridge, equipment, routing and
accepted persisted exports remain open until one changed pinned measurement.

### Actual source-pinned leaf-repacking measurement

H.3.6.13 checks the single changed physical80 run at `9632549`,
[run38425518357](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38425518357).
All original supplied source bytes, prepared program/hints and eleven stage
hashes match prior measurements. The captured family has232 shapes,992
rectangle records and68,460 canonical bytes; its exact SHA-256 is
`68351aa5c9af17d80a322b1effc6e9d2e6c7b1358a79c4229c3a5f10f297e5e7`.
All four appended staff stacks are present, along with eight selective turns.
All previous ordered shape hashes remain except four `B/staff_zone` shapes
(indices8-11):48 of its52 previous hashes remain. The frozen-source pure
append-only helper check and a fresh production family are distinct evidence.

Contact pruning adds2,776 clauses; support has936 rows/5,616 cells, with
14,784 upper-bound rows below the16,384 limit. The final actual model has1,709
variables/7,989 constraints and returns INFEASIBLE in1.039395 s, with10,426
branches and12 conflicts. This proves only infeasibility of the captured finite
family. Original-source infeasibility, equipment, routing, exports and saved80
bundle acceptance remain unproved/unreached.

Overall elapsed is58.627444 s, with56.877658 s in room search. This run used
2 s/one worker for the final global model; the previous `4604a10` run used
10 s/four workers. Fresh candidate drift and changed controls prevent causal
timing attribution. The same captured family cannot become feasible by merely
increasing its solve budget after actual INFEASIBLE. A useful next attempt
requires a justified family/source-preserving change and independent checks.

All eight extracted file bytes and the downloaded archive SHA are independently
verified. [Compact evidence](acceptance/9632549.json) retains the negative result;
raw local artifacts remain under `out/checkpoints/9632549-physical80-ci/`.

### Optional supervised finite-conflict evidence

C.5.5 adds default-false manual runner/workflow diagnostics with a separate
bounded JSON budget. [Contract](FINITE_CONFLICTS.md). Small actual core,
deletion-witness, exhaustion, accepted saved-QA and default-parity checks pass.
No full80 run was dispatched. The single diagnostic deadline includes original
candidate preparation; expiry yields UNKNOWN rather than a new deadline or
an unsupported whole-building infeasibility claim. Production limits and
automatic CI load remain unchanged.

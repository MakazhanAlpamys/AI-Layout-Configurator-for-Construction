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
supervision, fingerprints and reporting. Its [raw report](../research/scale80/2026_10_12/windows_warmstart.json)
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
The [raw Linux report](../research/scale80/2026_10_12/linux_warmstart.json)
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
and cannot establish whole-program infeasibility. Physical zone strategies
explicitly reject the unimplemented hint/cache bridge before building a model.

Actual room-area and equipment-width edits on a six-room, two-cluster source
repair stale geometry and pass every current gate. Root verification covered
54 tests and 49 subtests across incremental requests, stage evidence, hints,
cache and both zone dispatch strategies. Empty/disabled options preserve default
generation output. CLI/API/editor exposure, recursive zone guidance and measured
80-room edits remain separate follow-up work.

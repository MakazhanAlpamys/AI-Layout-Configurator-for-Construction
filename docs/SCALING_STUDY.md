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

## Reproduction

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\scale_20_rooms.yaml --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\scale-20 --variants 2 --max-attempts 3 --time-limit 40 --seed 42
```

Machine-readable results of the series are saved to
`out/scale-study/scale-report.json`: settings, timings, accepted candidates and
grouped rejection reasons for each run.

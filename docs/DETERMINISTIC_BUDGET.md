# Reproducible run: `--deterministic-budget`

## Problem

`--time-limit` sets a wall-clock budget. That is convenient when you need
variants within a predictable time, and that is what the flag has always meant. But such
a run is not reproducible: the search stops wherever the machine happened to be when
the seconds ran out, so two runs with the same seed return different layouts.
Recorded as `RP-01`.

The meaning of `--time-limit` has not changed. For reproducibility a separate
mode was added: `--deterministic-budget UNITS`.

## What the mode does

`--deterministic-budget` spends a **machine-independent amount of work** of CP-SAT
(`max_deterministic_time`) instead of seconds. The same input, seed and budget give
the same result on any machine; a slow machine simply takes longer.

Together with the budget, the search settings are pinned
([`search.py`](../src/layout_configurator/search.py)):

- `num_search_workers = 1` — portfolio search interleaves threads and would destroy
  repeatability at any budget;
- `random_seed` — from `--seed`;
- the wall-clock limit is removed: a remaining wall-clock cap would again
  cut the search off at a machine-dependent point.

The flag is available on `generate`, `generate-building` and `acceptance-building-matrix`.
`manifest.json` and the matrix report record `deterministic_units` and the
`repeatable` flag.

## Required condition: stable model order

A budget alone is not enough. CP-SAT explores the model in the order in which it
was built, so the model must also be built identically.

`LayoutIR.relation_pairs` returned a `set`, and the iteration order of a set of strings
depends on `PYTHONHASHSEED`, which Python chooses at random for each process.
Because of this, adjacency constraints entered the model in different orders, and two
processes with the same seed built **different models**. Within a single process the effect
is not visible: there the hash seed is constant, so the unit test for repeatability passed, while
real runs diverged.

Verified by serializing the model: before the fix `PYTHONHASHSEED=1/2/3` produced three
different digests, after it — one and the same, with the same objective. The method now
returns a sorted tuple.

## How to choose the budget size

A budget unit is not a second. The ratio depends on the machine and the model. Measurement
of 2026-09-06 on the development machine **before the migration of 2026-09-10**: Windows 11,
Python 3.11.15, OR-Tools 9.15.6755, pilot
`examples/pharma_cleanroom_pilot.yaml` (13 rooms):

| Budget | Wall-clock time | Objective |
| --- | --- | --- |
| 2 units | 8.0 s | 5 243 385 |
| 5 units | 19.5 s | 42 700 |
| 10 units | 34.4 s | 42 597 |
| `--time-limit 30` | 30.0 s | 42 597 |

That is, on that machine ≈ 3.4 s per unit, and **10 units roughly corresponded to
the former 30 seconds**. After the move to a different laptop on 2026-09-10 this
ratio was not re-measured, so it must not be used for CI calibration without a
new measurement. A small budget returns a feasible but poor solution
(2 units → objective a hundred times worse), so the budget must be tuned to the
program rather than set to the minimum.

## Limitations

- **Units are not seconds.** The wall-clock duration of a run on another machine will
  differ. CI timeouts must not be planned from it without a margin.
- **Repeatability is tied to the OR-Tools version.** Changing the solver version may
  change the result at the same budget; this is expected and is not a defect.
- **Repeatability does not mean optimality.** The budget cuts the search off at the
  same point every time; it does not carry it to a proven optimum.
- **The mode does not bypass candidate selection.** `generate-building` still
  publishes only variants that passed all gates; with an insufficient budget
  the set may fail to assemble. The `generation-failure.json` report is then also
  reproduced byte for byte.
- **Different seeds remain different.** The mode pins a run; it does not make the seed
  irrelevant.
- **Other sources of nondeterminism are not guaranteed.** The order of adjacency pairs
  was checked and fixed; if a new set iteration is added to the model, the mode
  will diverge again. The invariant is held by the test
  `tests/test_search_budget.py::ModelOrderStabilityTests`.

## Verification on the pilot

```powershell
.venv\Scripts\python.exe -m layout_configurator.cli generate-building `
  examples\pharma_cleanroom_pilot.yaml --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\rp01\a --variants 2 --max-attempts 9 --deterministic-budget 10 --seed 1
```

The same call into a second directory. Result of the two runs in separate processes
(203 s and 278 s — different wall-clock time at the same budget):

| What was compared | Result |
| --- | --- |
| `layout` of both variants | identical, objective 42525 and 42306 |
| `equipment` | identical |
| `flow_routes` | identical |

The failure case was checked separately: with a deliberately small budget both runs
failed identically, and `generation-failure.json` matched byte for byte, including
the whole `generation` section — that is, failure is reproduced too, not only success.

### Exchange files match in content, but not byte for byte

The SHA-256 of the DXF and IFC files differ, and this is expected: the difference is only in write
metadata.

- IFC: 2 lines out of 2928 differ — the timestamp in `FILE_NAME`. All entities and
  `IfcGloballyUniqueId` values are identical, so BCF references to the IFC remain valid
  across rebuilds.
- DXF: 12 lines differ — the document creation date and `$FINGERPRINTGUID` /
  `$VERSIONGUID`, which ezdxf generates on every write.

Therefore compare content (`layout`, `equipment`, `flow_routes`,
IFC entities), not file hashes. The hashes in
`review/*.artifact-inventory.json` are still needed, but for a different purpose: they
pin **the specific files handed to the reviewer**; they do not prove
reproducibility.

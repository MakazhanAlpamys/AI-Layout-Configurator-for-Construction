# Next plan — from a working pilot to a product people rely on

Written 2026-09-27. It orders the work that remains after the pharma-cleanroom
pilot, the hierarchical room solver and the English documentation. Each item
has a done-criterion that can be checked, not a feeling. Status is updated in
place; dated measurements go to [HANDOFF.md](HANDOFF.md).

The three things that decide whether the tool is taken seriously are **scale**
(real objects, not a 13-room demo), **trust** (reviewers and receiving tools
accept the output) and **usability** (a planner, not a programmer, can drive
it). The order below follows that.

## 1. Scale — real objects in seconds

| # | Work | Done when | Status |
| --- | --- | --- | --- |
| 1.1 | Hierarchical room solver (clusters → shapes → top level) | pilot and 20 rooms pass all gates on seeds 1/7/42 | **done** 2026-09-27 |
| 1.2 | 30–40 rooms: `rotatable` rooms, free-size single rooms at the top level, parallel top-level search in wall-clock mode | 30 and 40 rooms pass all gates on seeds 1/7/42 with `--time-limit 40` | **done** 2026-09-27; 40 rooms needs an idle machine at 40 s |
| 1.3 | Flow-aware search: route length and door contention in the objective; reject flow-impossible candidates inside the search, not after it | share of candidates rejected by `FLOW_COMPLETENESS` drops to zero on the scale series | criterion met 2026-09-27 (0 rejections on pilot, 20, 30 and 40 rooms after the door-approach fix and the hierarchy); route length now ranks variants in 3.3 |
| 1.4 | Multi-storey facility programs: floors, shared structural grid, stairs, lifts and shafts through `generate-multifloor` for `BuildingIR` | a two-floor 40-room program passes the same gates per floor plus vertical-core checks | open |
| 1.5 | 60–100 rooms: a third level (zones → clusters → rooms) and incremental re-solve after an edit | 80 rooms in under two minutes on CI hardware | open |

## 2. Trust — output that reviewers and tools accept

| # | Work | Done when | Owner |
| --- | --- | --- | --- |
| 2.1 | The three external pilot reviews (process/QA, cleanroom/HVAC, architecture/BIM) on the generated dossiers | three signed decision blocks, findings turned into issues | people (not code) |
| 2.2 | Open DXF and IFC in Revit, AutoCAD/BricsCAD and ArchiCAD; fix every import defect | a screenshot and a defect list per tool, no open blocker | people + development |
| 2.3 | Rule packs sourced clause by clause: EU GMP Annex 1, ISO 14644-1/-4, the relevant SN/SP; each rule carries source, edition and quoted clause | `check --require-provenance` passes for the pharma profile | development, reviewed by an engineer |
| 2.4 | Deterministic budget recalibrated on CI hardware; decide whether the CI matrix runs in repeatable mode | a measured units-per-second table for the CI runner | development |

## 3. Usability — a planner can drive it

| # | Work | Done when |
| --- | --- | --- |
| 3.1 | Input without YAML: text brief (parser exists) and a browser form that writes the same `BuildingIR` | the pilot can be entered in the browser and regenerates identically |
| 3.2 | Browser editing for facility results: move/resize a room, re-run equipment, routing and gates instantly, keep the BCF history | an edit round-trip under two seconds on the pilot — **done** 2026-09-27: `ui --edit`, 0.4–1.1 s per edit on the pilot, revisions saved to `revisions/rev_NN/` |
| 3.3 | Side-by-side comparison of variants: area deviation, route lengths, conflicts, clean/dirty separation | a comparison view and a JSON/CSV export — **done** 2026-09-27: `compare-variants` (JSON, CSV, PDF); variants are now forced to differ |
| 3.4 | A client-facing PDF report "why this variant": metrics, gate evidence, open issues, disclaimer | generated for every accepted bundle — first version in `comparison.pdf`; next: per-check gate evidence pages |
| 3.5 | Import an existing plan (DXF room polylines or IFC spaces) as a fixed starting point | a re-plan of an imported pilot passes the gates |

## 4. Product

| # | Work | Done when |
| --- | --- | --- |
| 4.1 | Laboratory, hospital and industrial profiles, each with a sourced rule pack and its own acceptance pilot | one accepted pilot per typology |
| 4.2 | An HTTP API around generate / check / review with the same JSON contracts, plus a container image | the pilot runs end to end through the API in CI |
| 4.3 | Two or three before/after case studies on real projects | published with the owners' permission |

## Order of work

1. ~~Finish 1.2 and put the scale series into CI as a nightly job~~ — done
   2026-09-27: `.github/workflows/scale.yml` runs 20/30/40 rooms nightly and on
   demand.
2. 1.3 flow-aware search — the scaling study showed that a better objective
   value can still fail the gates.
3. 3.3 and 3.4 — comparison and the client report make the output usable today
   and make the external reviews (2.1) easier.
4. 3.2 browser editing, then 1.4 multi-storey.
5. 2.3 sourced rule packs and 4.1 new typologies, one typology at a time.

2.1 and 2.2 need people and can start in parallel at any time: the dossiers and
the bundles already exist.

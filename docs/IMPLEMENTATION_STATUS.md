# Product-plan execution checkpoints

This ledger tracks [PRODUCT_PLAN.md](PRODUCT_PLAN.md) and
[NEXT_PLAN.md](NEXT_PLAN.md). [ROADMAP.md](ROADMAP.md) remains the archive of
the completed residential MVP. A completed checkpoint closes its named
behavior, not every requirement of its parent cycle.

## Baseline — 2026-10-12

Development was synchronized with `origin/main` at `f598723`. That baseline
already includes cluster-based hierarchical placement, facility editing with
revision snapshots, variant comparison, and multi-floor facility programs.
Its [main acceptance CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/36457857578)
and [20/30/40-room scale CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38228662745)
completed successfully. These runs verify that commit; later checkpoints need
their own evidence. The old GitHub billing blocker is no longer current.

## Checkpoints

| ID | Plan requirement | Result | Verification | Status |
| --- | --- | --- | --- | --- |
| C.4.1 | Flow graph and routing | A blocked shortest room chain triggers bounded, deterministic search of alternate chains. Door width, walls and equipment clearance stay mandatory. Exhausted search retains negative evidence and does not establish mathematical infeasibility. | `python -m unittest discover -s tests -p 'test_flow*.py' -v`: 16 tests; `test_facility.py`: 22; `test_pharma_cleanroom_acceptance.py`: 14 | complete 2026-10-12 |

## Remaining implementation

- B: canonical orthogonal room geometry, component geometry and a selectable
  backend alongside rectangles.
- C: preferences in both room backends; zone-driven placement and containment;
  structured, per-stage constraint explanations; cutouts and structural-grid
  support in the hierarchy.
- D: drawing-profile and symbol catalogs, editable dimension chains, separate
  zone/equipment/flow/egress sheets, sheet-set legends and revision history.
- E: semantic IFC zones and declared systems; matching exchange validation and
  independent receiving-tool review.
- F: retain scoped, sourced rule packs and UNKNOWN/IDS separation; add domain
  acceptance examples without claiming unperformed expert sign-off.
- G: source-backed DXF/IFC and vector-PDF imports, draft raster extraction,
  object confirmation, and LLM-to-typed-edit validation.
- H: unified multi-storey IFC and shared grid, typed vertical connectors,
  service links, versioned caching, batch jobs, HTTP API and private deployment.
- I.1–I.6: production program; unified BuildingIR extensions; source-backed
  engineering library; scenario and pressure-network checks; synchronized
  drawings/schedules; medical-plastics benchmark and change scenario.

## External acceptance

The process/QA, cleanroom/HVAC and architecture/BIM decision blocks remain
open until the named reviewers actually review the pinned bundles. Receiving
CAD/BIM screenshots, approved real-project case studies and source-use rights
cannot be established by unit tests. CI budget calibration remains a measured
runner task. Programmatic completion and these decisions are recorded separately.

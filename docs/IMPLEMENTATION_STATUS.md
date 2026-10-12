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
| C.2.1 | Preferred room relations | Both room backends rank area accuracy before preferred contacts, then position. Local and cross-cluster preferences survive hierarchy compilation; hard constraints remain mandatory. Inputs exceeding safe integer objective bounds are rejected explicitly. | `test_preferences.py`: 8; `test_core.py`: 10; `test_hierarchy.py`: 12; `test_search_budget.py`: 6 | complete 2026-10-12 |
| E.1 | Native IFC facility groups and valid spatial exchange | Functional zones retain direct space membership and nested groups; declared process systems group their routes and explicit equipment endpoints with stable IDs. Swept profiles, space aggregation, proxy types and owner history satisfy IFC4 EXPRESS rules. | Fresh root run: `pytest tests/test_ifc_facility_groups.py tests/test_exports.py -q`: 19 passed. Agent integration run: 76 passed and 4 subtests, including multifloor/API; single- and multi-floor strict validation: zero violations. | complete 2026-10-12 |
| C.1.1 | Hierarchical floor constraints | Final room footprints avoid cutouts, selected rooms honor structural axes, replayable axes survive fallback, and automatic cutout daylight needs a window-sized facade. Forced hierarchy solves the fixtures without fallback. | Fresh `pytest tests/test_hierarchy_constraints.py tests/test_hierarchy.py tests/test_preferences.py -q`: 33 passed, 14 subtests; agent multifloor regression: 3 passed. | complete 2026-10-12 |
| H.4 / NEXT 4.2 implementation | Persistent API, deterministic cache, batch and private container | HTTP jobs share canonical generation/export/QA and produce unsigned review dossiers. Persistence, cancellation, writer locking, immutable downloads and opt-in cache are supported. A private unprivileged Docker image and loopback Compose service are supplied. | Fresh HTTP/CLI tests: 12 passed, 4 subtests. Docker build and Compose validation passed; deployed container generated the 13-room pharma pilot in 6.52 s, full QA and PDF download passed. `scripts/api_smoke.py` and the CI container job retain repeatable evidence. Remote CI acceptance is pending. | implementation complete 2026-10-12; CI gate pending |

## Remaining implementation

- B: canonical orthogonal room geometry, component geometry and a selectable
  backend alongside rectangles.
- C: preferred zone groups; zone-driven placement and containment;
  structured, per-stage constraint explanations; monolithic cutout daylight
  facade parity.
- D: drawing-profile and symbol catalogs, editable dimension chains, separate
  zone/equipment/flow/egress sheets, sheet-set legends and revision history.
- E: independent receiving-tool review of semantic IFC zones, declared systems
  and spatial exchange; grouping does not establish engineered MEP design.
- F: retain scoped, sourced rule packs and UNKNOWN/IDS separation; add domain
  acceptance examples without claiming unperformed expert sign-off.
- G: source-backed DXF/IFC and vector-PDF imports, draft raster extraction,
  object confirmation, and LLM-to-typed-edit validation.
- H: unified multi-storey IFC and shared grid, typed vertical connectors,
  service links, non-editable package resources and remote container acceptance.
- I.1–I.6: production program; unified BuildingIR extensions; source-backed
  engineering library; scenario and pressure-network checks; synchronized
  drawings/schedules; medical-plastics benchmark and change scenario.

## External acceptance

The process/QA, cleanroom/HVAC and architecture/BIM decision blocks remain
open until the named reviewers actually review the pinned bundles. Receiving
CAD/BIM screenshots, approved real-project case studies and source-use rights
cannot be established by unit tests. CI budget calibration remains a measured
runner task. Programmatic completion and these decisions are recorded separately.

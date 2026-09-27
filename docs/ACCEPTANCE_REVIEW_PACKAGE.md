# External Acceptance Package — pharma-cleanroom pilot

## Purpose and boundary

The package is assembled for the three external gates in
[`PILOT_ACCEPTANCE_SPEC.md`](PILOT_ACCEPTANCE_SPEC.md): process/QA review,
cleanroom/HVAC review and architectural/BIM review. It contains the same
deterministic result in five projections: canonical JSON, DXF, vector
PDF, IFC4 and BCF 2.1.

The package is an **auditable early-design coordination package**. It is not a GMP
or ISO certification, a cleanroom qualification, an HVAC design, a CCS,
construction documentation or a building permit. A `PASS` status in it
means only that the stated project policy predicate passed.

## Contents

Directory `out/acceptance-2026-09-06/` (not version-controlled; reproduced by
the commands below):

| Path | Contents |
| --- | --- |
| `bundle/building_01..03.*` | three accepted variants for seed 1: JSON, DXF, PDF, IFC, BCF, coordination JSON |
| `bundle/manifest.json` | search parameters, seeds, accepted and rejected candidates |
| `acceptance-matrix-report.json` | matrix of seeds 1/7/42 × 3 variants, artifact hashes, cross-seed identifier reconciliation |
| `environment.json` | Python and solver library versions of the local run |
| `review/*.md` | one dossier per external gate for each variant |
| `review/*.artifact-inventory.json` | IFC entities, DXF layers and blocks, BCF contents and SHA-256 of the variant's files |
| `previews/building_0N.pdf.p1.png` | raster sheet previews (200 dpi) for quick viewing |
| `previews/building_0N.dxf.svg` | SVG render of the DXF via the native ezdxf backend |
| `viewer-qa/screenshots/` | 23 browser QA screenshots and five `*-observations.json` |
| `conflict-fixture/`, `history-fixture/` | **non-acceptance** states for checking the viewer, see [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md) |

## What has already been checked by machine

These checks have been run and do not need to be repeated by hand; they do not replace
expert judgement.

| Check | Command | Result 2026-09-06 |
| --- | --- | --- |
| Regression | `python -m unittest discover -s tests` | 134 tests, OK |
| Acceptance matrix | `acceptance-building-matrix ... --seeds 1 7 42 --variants 3` | all nine bundles accepted, cross-seed identity `PASS` |
| Bundle QA of each variant | `qa-building bundle\building_0N.json --profile ...` | PROGRAM/GEOMETRY/EQUIPMENT/FLOW/PROFILE/MANIFEST/DXF/PDF/IFC/BCF/COORDINATION — `PASS` |
| IDS exchange profile | `validate bundle\building_0N.ifc --ids ids\layout_baseline.ids` | 13/13 spaces, 50–51/… walls, 12/12 doors, 1/1 window, 13/13 openings — `PASS` |
| Facility policy | inside `qa-building` | 10 `PASS`, `ZONE_RELATIONS` = `NOT_APPLICABLE`, 0 issues |

IFC contents of each variant: 13 `IfcSpace`, 50–51 `IfcWall`, 12 `IfcDoor`,
1 `IfcWindow`, 13 `IfcOpeningElement` with `IfcRelVoidsElement`/`IfcRelFillsElement`,
90 `IfcRelSpaceBoundary`, 13 `IfcBuildingElementProxy` (5 equipment +
8 derived routes with `Pset_LayoutFlow`).

DXF layers: `A-WALL`, `A-DOOR`, `A-WINDOW`, `A-EQUIP`, `A-CLEARANCE`, `A-FLOW`,
`A-AXIS`, `A-DIMS`, `A-TEXT`, `A-TITLE`, `A-LEGEND`.

## Checklist: pharmaceutical process engineer / QA

Input: `bundle/building_01.json` (section `facility_validation`),
`previews/building_01.pdf.p1.png`, `bundle/building_01.coordination.json`.

1. Confirm the process stage vocabulary: `raw_material → production → packaging
   → finished_goods` and the branch `production → waste`. Record deviations as
   a program change, not as a geometry edit.
2. Confirm that the declared flow categories (`material`,
   `dirty_material`, `people`, `finished_goods`, `waste`) are sufficient for the
   real process.
3. Check the segregation policy: the pairs `material`/`dirty_material`,
   `material`/`waste`, `people`/`waste`, `finished_goods`/`waste` are treated as
   incompatible in shared intermediate rooms. Confirm or change
   this list.
4. Check room purposes and areas against the real equipment and
   headcount.
5. Explicitly record exceptions: temporal segregation, transfer and
   disinfection procedures are not represented in the model.
6. File comments as BCF topics (`bundle/building_01.bcf`), not as
   DXF edits.

## Checklist: cleanroom / HVAC engineer

Input: `bundle/building_01.json` (sections `spec.zones`, `facility_validation`),
`previews/building_01.pdf.p1.png`.

1. Check the zone classes and the "parent — child" cleanliness ordering;
   the tool checks only the vocabulary and the ordering and does not compute
   classification.
2. Check the declared pressure differentials and whether the 10 Pa guidance value
   from EU GMP Annex 1 applies to this strategy; the value is a project declaration.
3. Check the separate personnel/material airlock roles, their parent reference to
   the cleanroom, and room membership.
4. Confirm that the absence of a model for door interlocking, air changes,
   particle calculation and containment is an acceptable boundary for this stage.
5. Record HVAC zone requirements that cannot be expressed by the current
   model as input for the next cycle.

## Checklist: architect / BIM coordinator

Input: `bundle/building_0N.dxf`, `bundle/building_0N.ifc`,
`bundle/building_0N.pdf`, `bundle/building_0N.bcf`.

1. Open the DXF in the receiving CAD tool and the IFC in the receiving BIM tool;
   record whether the files opened without loss, and in which tool version.
2. Check the layers, equipment blocks, dashed clearances and the sheet title block.
3. Check the IFC: spatial structure, walls, openings, types,
   `IfcRelSpaceBoundary` and the property sets of equipment and routes.
4. Compare the three variants: room, equipment and flow identifiers
   must match, geometry must differ.
5. Bear in mind that the bundle is one of many admissible variants. Rerunning
   with the same seed yields different accepted geometry (`RP-01`), so
   review the files pinned by hashes in
   `review/*.artifact-inventory.json`, not a rebuilt bundle.
6. Return comments in `*.bcf`; the next iteration carries topics that have disappeared
   into the new package as `Closed`.

## Reproducing the package

```powershell
# 1. Regression
.venv\Scripts\python.exe -m unittest discover -s tests

# 2. Acceptance matrix (source of the bundle and acceptance-matrix-report.json)
.venv\Scripts\python.exe -m layout_configurator.cli acceptance-building-matrix `
  examples\pharma_cleanroom_pilot.yaml `
  --profile rules\pharma_cleanroom_pilot.yaml `
  --output out\acceptance-2026-09-06\matrix `
  --variants 3 --seeds 1 7 42 --max-attempts 9 --time-limit 30

# 3. Independent re-check of each variant
.venv\Scripts\python.exe -m layout_configurator.cli qa-building `
  out\acceptance-2026-09-06\bundle\building_01.json `
  --profile rules\pharma_cleanroom_pilot.yaml

# 4. IFC check against IDS
.venv\Scripts\python.exe -m layout_configurator.cli validate `
  out\acceptance-2026-09-06\bundle\building_01.ifc --ids ids\layout_baseline.ids

# 5. Read-only review in the browser
.venv\Scripts\python.exe -m layout_configurator.cli ui `
  out\acceptance-2026-09-06\bundle --variant 1 `
  --profile rules\pharma_cleanroom_pilot.yaml
```

## Gate status as of 2026-09-06

| Gate | Owner | Status |
| --- | --- | --- |
| Process and quality review | process engineer / QA | **open** — package and checklist ready, review not conducted |
| Cleanroom/HVAC review | cleanroom / HVAC engineer | **open** — package and checklist ready, review not conducted |
| Architectural/BIM review | architect / BIM coordinator | **open** — DXF/IFC/PDF/BCF ready and passed machine read-back; opening in the receiving CAD/BIM tools not performed, they are not available in the environment |
| Viewer screenshot QA | browser runtime | **closed** — screenshots captured, projection unit defect VQ-01…VQ-04 fixed and the run repeated; non-blocking VQ-05…VQ-13 fixed (the last ones on 2026-09-26), see [VIEWER_QA_2026-09-05.md](VIEWER_QA_2026-09-05.md) |

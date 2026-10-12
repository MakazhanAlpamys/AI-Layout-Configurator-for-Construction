# Source-backed plan imports

```powershell
layout-configurator import-plan existing.dxf --output out/import-draft.json
layout-configurator generate-building program.yaml --output out/imported `
  --imported-plan out/import-draft.json --confirm-import `
  --import-reviewer "Geometry reviewer" --room-map survey-room canonical-room
```

The first command writes an unconfirmed DRAFT with source SHA256, declared
units, source entity IDs, assumptions and unresolved issues. Review it before
using the second command. `generate` accepts the same import options.
`--room-map SOURCE ROOM` may be repeated. Unmapped semantic IDs must already
match the canonical program. A reviewer name and explicit confirmation are
required each time geometry is used.

DXF imports closed straight axis-aligned rectangular polylines on `A-ROOM`
in modelspace. Exported outlines include `LAYOUT_IR` XDATA with `room_id` and
`room_type`; outlines without that data get provisional `dxf:<handle>` IDs.
Known `INSUNITS` are converted to millimetres. Unitless sources require
`--unit-scale-mm`; a scale contradicting declared units is an unresolved issue.

IFC imports actual world-coordinate rectangular space footprints, retaining
GlobalIds and semantic room metadata. Select one storey using
`--storey <GlobalId-or-name>` when the source contains multiple storeys.
Rotated, holed or nonrectangular footprints are rejected as issues rather
than replaced by bounding boxes.

Unsupported entities, overlap, duplicate IDs and malformed geometry remain
visible draft issues. Confirmation re-reads the unchanged original and checks
mapping, grid, bounds, cutouts, room dimensions and area ranges. It never
snaps geometry onto the grid. Validated rectangles enter the existing solver
as `fixed_rects`; all remaining placement, equipment, route and export checks
still apply. The draft's `.confirmation.json` sidecar and generated manifest
retain reviewer and source/draft/program hashes. This confirms geometry and
does not constitute engineering sign-off.

## Calibrated vector PDF

```powershell
layout-configurator import-plan survey.pdf --output out/pdf-draft.json `
  --page 1 --unit-scale-mm 100 --origin-pt 0 0
layout-configurator generate-building program.yaml --output out/pdf-imported `
  --imported-plan out/pdf-draft.json --confirm-import `
  --import-reviewer "Geometry reviewer" --room-map pdf:page1:op7 canonical-room
```

Use the candidate ID actually recorded in the draft. `--page` is one-based;
the calibration is millimetres per unrotated default PDF point. `--origin-pt`
identifies the page coordinates corresponding to canonical `(0, 0)`; x points
right and y points up. The importer does not infer scale or room identity from
labels. Every selected candidate needs an explicit mapping. Other rectangular
paths may be title frames or symbols, so they are not automatically rooms.

Only closed axis-aligned rectangular page-content paths are accepted. Page
rotation, nondefault UserUnit, annotations, forms, raster images, clipping,
curves and compound paths produce unresolved issues. Crop-crossing candidates
are rejected. Ambiguous overlapping candidates remain visible; overlapping
selected rooms cannot be confirmed. Confirmation re-extracts the unchanged
source, checks calibration and candidate selection, and records source, page,
content, draft, selection and program hashes before using fixed rectangles.

Limits are 20 MiB source, 8 MiB decoded page content, 20,000 operators and
1,000 candidates. Rectangular DXF/IFC supports at most 1,000 rooms and
100,000 IFC triangles per space. Raster/OCR, general PDF drawings and
arbitrary orthogonal footprints remain separate requirements.

## Raster source and unverified OCR

```powershell
layout-configurator import-raster survey.png --output out/raster-draft.json
layout-configurator import-raster survey.png --output out/raster-ocr.json `
  --ocr-tsv reviewed-engine-output.tsv
layout-configurator import-raster survey.jpg --output out/local-ocr.json `
  --ocr-tesseract --ocr-language eng --ocr-timeout 30
```

The independent raster draft identifies one PNG/JPEG by its exact source bytes
and normalized decoded RGB pixels. EXIF orientation is applied explicitly;
coordinates remain upright-image pixels with a top-left origin, x right and
y down. Resolution metadata and detected numbers never become millimetres,
scale, room boundaries or semantic room IDs.

OCR is optional. Without it, status is UNKNOWN. External exact UTF-8 Tesseract
TSV is hashed and rechecked on every write/read/review. `--ocr-tesseract`
explicitly runs an available local executable without a shell; an unavailable,
failed, oversized, timed-out or malformed response retains UNKNOWN diagnostics
and no partial words. `--tesseract-path` selects a local engine only with that
opt-in. Tesseract is not installed by the Python package.

Every word box is checked against actual upright image bounds. Text, hierarchy,
pixel boxes and numeric confidence remain **UNVERIFIED**, including confidence
100. `record_raster_review(...)` can record separate source/draft hashes,
explicit selected word IDs and a supplied reviewer name. It does not approve or
map canonical geometry. The fixed-room generation loader rejects raster drafts.

Limits are 20 MiB image source, 16 million pixels, 8,192 pixels per side,
2 MiB TSV, 10,000 TSV rows and 5,000 words; the local OCR deadline is at most
60 seconds. Actual local Tesseract 5.5.0 recognized the ROOM/9000 fixture on Linux
with retained source, decoded-pixel and TSV hashes. General raster boundary
reconstruction, explicit scale calibration and reviewed canonical room mapping
remain separate requirements.

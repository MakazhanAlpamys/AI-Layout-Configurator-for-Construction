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

This slice supports rectangular DXF/IFC, at most 1,000 rooms and 100,000 IFC
triangles per space. PDF, raster/OCR and arbitrary orthogonal footprints
remain separate plan requirements.

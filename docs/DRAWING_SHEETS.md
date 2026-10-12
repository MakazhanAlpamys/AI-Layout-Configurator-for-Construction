# Thematic vector sheet sets

Facility bundles now include `building_NN_sheets.pdf` alongside their main plan.
The manifest's `sheet_set` records the PDF name, SHA256 and every page's sheet ID,
theme, revision, scale, source variant and status.

`qa-building` independently reads an indexed sheet set when present. Its
`SHEET_SET_READBACK` check verifies the delivered hash, all thematic and
continuation identities, real page count, printed project/profile/variant and
revision fields, A3 scale captions, and readable vector content. Replacing PDF
text or pages still fails after recalculating the recorded hash. Legacy bundles
without a sheet index retain their original QA contract. This check verifies
artifact identity; geometric design and engineering acceptance have separate
scope.

The A3 landscape set contains functional zones, equipment/service envelopes,
process routes and egress source/candidates. Every plan shares canonical room
tags and millimetre geometry; readable legends link tags to source identities.
Dense schedules continue on indexed pages rather than omitting objects.
Profile axes, dimensions, title block and a true print-scale bar are retained.

Zones come from direct canonical memberships; the legend records ancestry.
Equipment footprints and dashed service envelopes come from supplied placements
and declared clearances. Process arrows come from supplied route points.
Incomplete equipment or missing declared routes produce UNKNOWN sheet status.

Egress remains UNKNOWN. An explicit external-entry opening allows door-graph
candidate paths to that source exit. These paths do not establish travel-distance,
clearance, occupancy or fire-safety compliance. Without that opening there is no
inferred exit or candidate path. All revision blocks describe the supplied
current revision; prior revision records and signatures are not manufactured.

The default embedded Noto Sans fonts support Cyrillic and Latin, including in
an installed wheel. Unmodified font files, OFL license, pinned official source
commit and hashes live in `layout_configurator/fonts`. A custom embedded TTF may
be supplied through `export_drawing_sheet_set(..., font_path=...)` or
`write_facility_variant(..., sheet_font_path=...)`; uncovered source glyphs are
rejected. API cache keys include font assets and PDF dependency versions.

The public exporter consumes the existing BuildingIR, LayoutResult, equipment
and routes. It requires rectangular room geometry until polygon drawing topology
is integrated. The current checkpoint does not supply a complete equipment-symbol
catalog, historical revision ledger or an approved evacuation drawing.

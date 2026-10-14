# Main-PDF equipment annotation placement

Checkpoint [QA.26](PDF_ROOM_LABELS.md) separately moves future room captions
away from actual body and native symbol ink. It freezes the current QA.24
equipment caption/guide choices against original room/dimension reservations
before selecting room positions. This document retains its original evidence.

Checkpoint QA.24 addresses a saved rectangular main-plan defect: the literal
`work (36.0 m2)` room label overlapped the `press` equipment identifier. The
change applies to `export_pdf()` and `export_building_pdf()` and future facility
bundles that use them. Previously retained acceptance artifacts remain intact.

The exporter reserves room labels and enabled room dimensions using the actual
Helvetica font metrics in page points. Equipment identifiers retain their
existing Helvetica-Bold font and size. A clear body-centre anchor is retained;
otherwise a finite sequence of thirteen vertical candidates is checked against
the plan bounds and reserved lettering. Earlier equipment labels and their
painted association guides also reserve space for subsequent lettering.

A displaced caption that still meets its literal body needs no guide. A
separate caption receives a blue guide from the nearest body edge, provided
the guide avoids existing lettering. Guide intersections with other guides are
permitted. Final equipment annotation reservations are passed to the existing
flow-label placer. The flow fixture checks the actual finalized equipment
baseline rather than assuming a particular repair offset.

An identifier that cannot fit this bounded placement scope raises `ValueError`
instead of publishing overlapping equipment text or text outside the plan.
The direct exporter saves only after successful drawing; a regression retains
previous destination bytes on this rejection. This is a presentation limit,
not evidence that the supplied layout or production program is infeasible.

The repair preserves literal room and equipment geometry, source placements,
clearances, routes, drawing settings and public exporter signatures. It runs
no solver. Independent PDF inspection reads text at actual paint operators
through graphics/text transforms, checks font-metric boxes and reads painted
wall/body paths. ReportLab coordinate serialization is allowed at most
0.001 point corner displacement in the source-geometry comparison.

This is a bounded equipment-caption repair. Room-to-room captions, reference
axes, dimension-chain lettering and arbitrary dense flow-label fallback remain
outside its general readability claim. The existing main-PDF fonts remain
Helvetica/Helvetica-Bold; multilingual portability belongs to a separate scope.
Thematic sheets and process documents keep their existing contracts and fonts.
Artifact QA, local geometry and professional acceptance remain separate gates.

## Verification

The original saved-PDF regression fails twice with actual room/equipment box
overlap. An independent ordering regression then exposes a 3.2375-point guide
intersection with the later caption. After the repair, six focused tests pass
in 0.86 seconds; the historical export file passes ten tests in 3.06 seconds,
and the drawing-symbol file passes six tests and ten subtests in 1.05 seconds.
Each file runs in a separate sequential foreground invocation.

Regular installed wheels from foreign working directories retain three old
and four new saved PDF examples. Original overlap measures 1.0081575,
120.9271 and 5.66474025 point2; all new room/equipment overlaps are zero.
The two new association guides avoid all measured lettering. Closed painted
geometry, original room text, font inventories, clear neighbor anchors and
six public exporter signatures match their original witnesses. Solver calls
remain zero. All seven pages are rendered and contact-inspected; all four new
pages and a 300 dpi leader detail receive separate visual inspection.
Final source/test bytes match the staged Git files after LF normalization;
all final-wheel page/detail rasters exactly match the inspected image bytes.

[Compact evidence](acceptance/1030887-pdf-equipment-labels.json) retains source,
archive, wheel and actual PDF hashes. Local raw proof lives under
`out/checkpoints/pdf-equipment-labels-qa24/`, with PDFs and renders under
`output/pdf/equipment-labels-qa24/`. Poppler emits Symbol/ArialUnicode fallback
warnings although the actual PDF font inventory contains only Helvetica and
Helvetica-Bold. Full acceptance is not run by this checkpoint.

The pushed `7c10594` checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38584729033)
in forty seconds including setup. Full jobs are skipped; changed full
acceptance remains pending.

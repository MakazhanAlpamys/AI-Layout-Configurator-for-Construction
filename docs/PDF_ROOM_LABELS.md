# Bounded Main-PDF Room Captions

Checkpoint QA.26 repairs room lettering crossed by equipment ink in future
rectangular main PDFs. Focused regressions, ordinary installed old/new wheels
and rendered artifact inspection pass for the bounded cases below. Complete
drawing and engineering acceptance remain separate.

[Compact evidence](acceptance/f41ec89-pdf-room-labels.json) retains exact source,
archive, wheel, original input, actual PDF and rendered-page identities.

## Observed artifact failures

The supplied clear fixture places the work-room caption at its original
center, `(9000, 3000)` mm. The press body occupies
`[9000, 10000] × [2600, 3400]` mm, so its left vertical stroke crosses the
room lettering. The existing equipment-caption repair separates text boxes
but does not reserve equipment ink for the room caption.

Root's actual saved-PDF regressions reproduced five failures in 1.17 seconds:
the observed body strike, a generic-symbol strike, contact with painted
half-width despite a clear centerline, final flow reservations and an
unplaceable full room caption. These were artifact failures after a separate
test syntax correction; the syntax error is not feature RED evidence.

A sixth independent regression reproduced a native `process_vessel` miter
strike in 0.73 seconds, with glyph-box/painted-ink overlap of
`0.03538523317535935` square points. Its small source frame exposes a join
extending beyond a plain body-plus-half-width reservation. This requires a
conservative symbol reservation without changing the actual symbol painter.

## Bounded caption placement

Room captions retain their complete literal text, existing Helvetica font
and existing size calculation. The planner measures their real glyph bounds
and checks width and height against the room's projected host and the plot.
It preserves a clear original center or tries thirteen deterministic vertical
positions. It does not shrink, truncate, hide or move a caption outside its
host to manufacture readability. Exhausted capacity raises
`Room annotation density exceeds readable plan capacity` before saving.

The existing [QA.24 equipment captions](PDF_EQUIPMENT_LABELS.md) and guides
are planned first against the original centered room and dimension boxes.
Those choices are frozen. Room placement then avoids the frozen equipment
caption/guide boxes and equipment ink, preserving current gear baselines and
clear anchors. Room dimension captions retain their existing text, font and
positions. Final room, dimension, equipment and guide reservations reach flow
label placement.

The physical equipment body uses its unchanged `0.8` point stroke. Plain
bodies reserve four edge strips with `0.4` point half-width, including their
rectangle corners. Assigned native symbols reserve their literal body frame
plus `0.4 × 10 = 4` points, using the existing default miter limit of ten.
This is a conservative capacity reservation; it does not enlarge the painted
body or symbol. Equipment guides retain their actual `0.4` point stroke and
`0.2` point half-width reservation.

Literal walls, doors, equipment bodies, clearance outlines, selected symbols
and drawing routes remain fixed. Public export signatures and current fonts
remain unchanged. Annotation planning neither solves a layout nor edits its
source objects. A failed caption placement preserves existing destination
bytes and the original supplied source.

## Independent readback and verification

The regression oracle reads saved PDF font metrics, text paint, CM/TM
transforms and path paint state. It reconstructs actual solid stroke width,
caps, joins and miter limits. It compares glyph bounds with painted ink,
rather than taking expected positions from the producer's annotation helpers.
Literal paths and styles, frozen equipment captions, clear room anchors,
host/plot containment, final flow reservations and protected destinations are
checked separately.

This checkpoint covers straight body/guide paths and the native symbol
polylines. The oracle explicitly rejects curves instead of silently omitting
them. Dashed clearance and flow paths are checked for unchanged geometry and
style; general dashed-ink avoidance is not established.

The final focused file passes six tests in 1.08 seconds, and the final symbol
file passes six tests and ten subtests in 1.09 seconds. The unchanged plain-body
and caption paths pass six previous equipment tests in 0.95 seconds and ten
export tests in 3.48 seconds before the assigned-symbol-only miter adjustment.
Every invocation uses one file in the foreground, sequentially.

Ordinary installed wheels imported outside the checkout retain eight cases
each: room-only, observed body, coincident generic symbol, stroke grazing,
tiny native miter, rotated vessel, real supplied flow and oversized identifier.
All seven drawing pairs preserve actual painted paths, graphics transforms,
stroke/dash/cap/join/miter state, font inventories, clear entry text and frozen
equipment captions. Six public signatures match, source/model bytes remain,
and both CP-SAT aliases record zero calls. Body, generic, grazing, tiny-miter
and rotated-vessel overlap measures 8.14, 13.03036649, 2.0339825, 0.03538523
and 8.24241853 square points before repair, and zero afterward. The old
oversized output remains negative evidence; the repaired exporter retains
the exact previous valid PDF on rejection.

All sixteen retained pages are rendered and inspected across three contact
sheets. Seven newly authored plans receive separate full-page inspection;
the protected destination is byte-identical to the inspected clear PDF.
Room text stays within its host, and the real flow caption remains separate.
The tiny miter result comes from saved stroke geometry; a whole-page raster
cannot resolve that small area. Final LF source/test bytes equal the Git index
and the installed exporter. Raw proof remains under
`out/checkpoints/pdf-room-labels-qa26/`; PDFs and renders remain under
`output/pdf/room-labels-qa26/`.

Retained I.5.5.2 PDFs remain byte-identical to their original checkpoint
artifacts and are not rewritten. Future exports use the corrected caption
placement. General dense annotations, arbitrary curves and dashed graphics,
equipment captions against native symbol ink, main-PDF font portability,
professional drawing review and the changed full
acceptance gate remain open. This caption repair does not lift production,
full process, installation, three-dimensional or engineering barriers.

The pushed `3627520` source checkpoint passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38628626452)
in forty-two seconds including setup. Complete collection, exhaustive four-group
partition and four sequential contract files pass; all full jobs are skipped.
Changed full acceptance remains pending.

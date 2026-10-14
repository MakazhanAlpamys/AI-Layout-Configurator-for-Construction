# Named drawing presentation profiles

Facility rule packs can select a drawing preset and supply explicit overrides:

```yaml
drawing:
  preset: equipment_service
  sheet_id: E-201
  title: Equipment coordination review
  revision: P02
  show_structural_grid: true
```

The profile is expanded into the existing `FacilityDrawingProfile` before
generation, DXF/PDF projection, bundle indexing and cache fingerprinting. Current
canonical serialization retains every effective setting. Source geometry,
equipment placement, routes and rule thresholds are unaffected.

Main rectangular PDFs use bounded source-font reservations to separate
equipment captions from room lettering and earlier equipment guides. Clear
anchors remain fixed; unplaceable identifiers reject before saving.
[Placement and verified scope](PDF_EQUIPMENT_LABELS.md).

| Preset | Room dimensions | Equipment service envelopes | Flow labels and legend | Structural grid |
| --- | --- | --- | --- | --- |
| `coordination_review` | shown | shown | shown | shown |
| `architecture_dimensions` | shown | hidden | hidden | shown |
| `equipment_service` | hidden | shown | hidden | shown |
| `process_flows` | hidden | hidden | shown | shown |

Explicit recognized settings override the preset. Named profiles reject unknown
names/fields, coerced booleans, blank/nontext metadata and nonfinite or negative
legend offsets. Existing rule packs without a preset retain their previous
behavior. `drawing_profile_catalog()` returns independent effective mappings;
`resolve_drawing_settings(mapping)` also offers a strict programmatic boundary.

The architecture preset also enables source dimension chains. Other presets
retain their previous annotation settings unless explicitly overridden.

These are project presentation presets, not jurisdiction standards or engineering
approval. Hiding an annotation never removes its validation requirement. The
thematic sheet set still includes every theme and its source inventory. Complete
discipline-specific symbol libraries remain a separate Cycle D requirement.

## Explicit equipment and sanitary symbols

An optional equipment-ID mapping selects project schematic symbols explicitly:

```yaml
drawing:
  preset: equipment_service
  equipment_symbols:
    mixer_01: process_vessel
    basin_01: washbasin
```

The initial catalog is `generic`, `rectangular_unit`, `process_vessel`,
`washbasin`, `toilet` and `workbench`. `drawing_symbol_catalog()` returns fresh
normalized vector paths and their scope. The equipment type does not infer a
symbol. Unknown names or equipment references fail before export.

DXF equipment blocks and PDF overlays retain the exact declared physical frame;
selected detail scales inside it and follows the actual permitted quarter-turn.
DXF inserts retain the source equipment ID and selection in `FLC_SYMBOL` XDATA.
The equipment-sheet legend prints the selection and its project scope. Symbol
assignments enter canonical drawing serialization and cache fingerprints.

These schematic paths are not manufacturer geometry or engineering approval.
They do not change equipment dimensions, placement, service clearances, routes
or rule thresholds. Absent assignments preserve the existing drawing behavior.

## Source dimension chains

`show_dimension_chains: true` adds ordered coordinate chains for projected room
edges, declared structural axes and overall source extents. It is enabled by
`architecture_dimensions`; the legacy default is false and retains its canonical
serialization. The switch is strictly boolean and can be overridden explicitly.

Room-edge stations project all actual rectangular room edges onto each axis;
they do not assert that the intervals form one continuous facade. Coordinates
and fractional millimetres are preserved. Native DXF `DIMENSION` entities on
`A-DIMS` retain editable definition points, automatic measurement text and stable
source identities/hashes in `FLC_DIM`. Native overall dimensions replace the
legacy overall line/text pair when enabled.

PDFs draw the same measured spans as vectors. Thematic schedules list every
source interval; dense values remain in the complete schedule when inline text
does not fit. Paper-space gutters keep chains clear of grid labels, and the page
scale accounts for their bounds. Projection is bounded to 1,000 segments.

`validate_dimension_chains_dxf()` independently reads actual definition points,
millimetre units, inventory, metadata, native measurement and measurement factor.
Opted-in full bundle QA adds `DIMENSION_CHAINS_READBACK`; absent chains preserve
legacy checks. Native CAD/BIM receiving-tool acceptance remains an external gate.

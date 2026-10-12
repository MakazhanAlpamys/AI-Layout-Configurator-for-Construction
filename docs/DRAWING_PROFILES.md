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

These are project presentation presets, not jurisdiction standards or engineering
approval. Hiding an annotation never removes its validation requirement. The
thematic sheet set still includes every theme and its source inventory. Complete
discipline-specific symbol libraries and editable dimension chains remain
separate Cycle D requirements.

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

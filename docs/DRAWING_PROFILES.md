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
equipment/sanitary symbol catalogs and editable dimension chains remain separate
Cycle D requirements.

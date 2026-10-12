# Typed program proposals

Structured assistant or manual commands become reviewable program drafts.
The adapter accepts an `edits` array with `set_room`, `set_equipment` and
`set_flow` operations, existing target IDs and an explicit field catalog.
Numeric changes use `{ "value": 16, "unit": "m2" }` for areas or `mm` for
lengths. Coordinates, exports, rule changes and compliance verdicts are
outside the catalog. The complete contract is
[proposed_edits.schema.json](../schemas/proposed_edits.schema.json).

```json
{"edits":[{"operation":"set_room","target_id":"work","changes":{
  "target_area":{"value":16,"unit":"m2"},
  "min_area":{"value":16,"unit":"m2"},
  "max_area":{"value":16,"unit":"m2"}
}}]}
```

```powershell
layout-configurator propose-edits program.yaml edits.json `
  --source-text request.txt --source-kind ai_proposal --producer assistant `
  --output out/proposal.json
layout-configurator apply-edits program.yaml out/proposal.json `
  --confirm --reviewer "Program reviewer" --output out/updated.json
layout-configurator generate-building out/updated.json --output out/regenerated
layout-configurator qa-building-set out/regenerated --json
```

The draft preserves the request, producer and source/base hashes. Confirmation
requires a named reviewer and the exact canonical base. Application validates
LayoutIR, BuildingIR and all edited references atomically, then writes a new
canonical program and `.application.json` evidence. The evidence explicitly
marks old geometry/artifacts invalid and solver/checks pending. Regenerate
from the updated program and check the new bundle before using its artifacts.

The contract can consume structured AI proposals, but these commands do not
call an external model or infer edits from the request text. Source text is
provenance. Stale drafts, unknown fields, wrong units, non-finite quantities,
duplicate field edits and missing confirmation are rejected. Confirmation
does not constitute an engineering or regulatory verdict.

# Source-bound production program

Cycle I.1.1 introduces the versioned contract in
`schemas/production_program.schema.json` and
`production_program.validate_production_program(raw, building=building)`.
Checkpoint I.1.1 validates a standalone program against an existing canonical
BuildingIR. I.1.2 adds explicit source-only embedding and a validation CLI.

Operations reference existing rooms, equipment, flows and material states.
Equipment must belong to the operation's room, and a named flow must have an
actual source endpoint belonging to it. Four explicitly typed measurements
retain supplied cycle time, throughput, movement frequency and operator count,
their units, source IDs and fact/assumption/confirmation basis. Missing values
remain explicitly `UNKNOWN`; capacity is not inferred from these declarations.

Sources retain exact UTF-8 content, edition, locator, usage-rights declaration
and SHA256. The program binds the original canonical building digest. Duplicate
IDs, unknown references, source/excerpt substitutions, stale building hashes,
invalid calendars and stale confirmation revision/source hashes fail binding.
Counts, nested records and combined source/contract bytes are checked before
unbounded serialization. Original booleans and numeric scalars are validated
before normalization can hide malformed declarations.

Each required or forbidden room-adjacency requirement records its originating
operation, source excerpt and reason. A missing or opposite original relation
produces evidence `FAIL`. This checker verifies the declarations; geometry is
`NOT_EVALUATED`. Assumptions, missing measurements, unresolved usage rights and
unconfirmed claims produce evidence `UNKNOWN`. Confirmation must name the same
source ID as each claim, even when two sources have identical content hashes.

The returned detached contract preserves original JSON scalars and source
newlines. Scoped `PASS` never authenticates a supplied reviewer record:
authenticated approval remains `UNKNOWN` and engineering acceptance `PENDING`.
Actual process/QA agreement, applicable engineering requirements and downstream
scenario/geometry enforcement remain separate work.

`examples/production_program_reconstruction.json` is an explicitly labeled
reconstruction bound to the unchanged canonical pharma pilot. Its operations,
equipment and existing relations resolve, but its load assumptions and usage
rights remain `UNKNOWN`. It supplies neither an approved manufacturing program
nor the medical-plastics acceptance benchmark. Fresh independent validation
retains 18 unresolved evidence items and the exact pilot source hash.

## Embedded source validation

Add `production_program` to a complete canonical BuildingIR JSON/YAML document.
Its `building_sha256` binds the canonical building with this field omitted;
the complete source serialization retains both the building and the program.
Admission rejects any normalization that changes the original canonical scalar
types or fields. Source files must have unique string keys; YAML mapping merges
are rejected. Combined file, object, reference and nested-record limits apply
before constructor normalization.

```console
layout-configurator validate-production-program source.json --output program-report.json
```

Exit codes are `0` for scoped declaration `PASS`, `4` for `UNKNOWN`, and `2`
for `FAIL` or malformed input. Reports retain source/operation/requirement IDs;
geometry remains `NOT_EVALUATED` and engineering acceptance `PENDING`.

Python callers use `admit_embedded_production_program(raw)` to receive the
source object and report, or `BuildingIR.from_mapping(raw,
include_production_program=True)`. Ordinary constructors, API/form validation
and orthogonal source readers reject embedded programs. Generation and
incremental repair stop before CP search when an admitted source carries these
unsupported process requirements; independent facility QA records an explicit
`PRODUCTION_PROGRAM_CAPABILITY` result of `UNKNOWN`. Removing the program
during an incremental edit does not bypass this gate.

Fresh root verification passed 80 related tests in two runs. An ordinary wheel
built from pinned `4604a10` plus the eight frozen I.1.2 files was installed into
a separate environment. From a foreign working directory, all seven packaged
implementation hashes and both authoritative schema resources were checked;
offline schema composition and actual PASS/UNKNOWN/stale/contradictory CLI
cases passed. The reconstruction retained all 18 unresolved evidence items.
This local installed-wheel evidence does not extend the earlier `c0eb166` CI pin.

I.2.1 now reuses this exact source/program identity for explicit typed transfer
sidecars and independent actual route permission checks. Original flow labels
remain unchanged. [TRANSFER_PROGRAM.md](TRANSFER_PROGRAM.md) describes the
personnel-passbox rejection, bounded admission and unverified mechanisms.

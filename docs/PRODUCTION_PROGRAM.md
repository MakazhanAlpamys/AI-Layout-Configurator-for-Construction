# Source-bound production program

Cycle I.1.1 introduces the versioned contract in
`schemas/production_program.schema.json` and
`production_program.validate_production_program(raw, building=building)`.
This first checkpoint validates a standalone program against an existing
canonical BuildingIR. Embedded admission and a validation CLI are subsequent
integration steps.

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

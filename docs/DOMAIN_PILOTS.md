# NEXT 4.1 domain pilot software acceptance

Three repository-owned, coordinate-free BuildingIR 0.2 project briefs now exercise
the existing laboratory, hospital and industrial 0.1 coordination profiles.

| Pilot | Rooms | Functional zones | Equipment footprints | Declared flow categories |
|---|---:|---:|---:|---:|
| `examples/laboratory_pilot.yaml` | 5 | 3 | 2 | 4 |
| `examples/hospital_pilot.yaml` | 7 | 4 | 2 | 5 |
| `examples/industrial_pilot.yaml` | 7 | 4 | 2 | 6 |

Each source header identifies project brief P01 as the origin of all numerical
room, opening, route and equipment requirements. These fixture dimensions are
neither legal limits nor manufacturer data. No room coordinates are supplied.
Compatible categories may share a channel; incompatible categories use separate
transfer rooms. Actual generated routes contain three rooms, including a real
intermediate room, so separation acceptance does not rely on direct routes with
an empty intermediate-room set.

```powershell
.venv/Scripts/python.exe scripts/domain_pilots_acceptance.py --output out/domain-pilots
.venv/Scripts/python.exe scripts/domain_pilots_acceptance.py --output out/domain-pilots --check-only
.venv/Scripts/python.exe -m pytest tests/test_domain_pilots.py tests/test_facility.py tests/test_building.py -q --tb=short
```

The runner calls the actual CLI/shared bundle writer, independently rechecks the
persisted bundle set and runs IfcOpenShell validation with `express_rules=True`.
It writes `qa-report.json` per domain and a combined `acceptance-report.json`
containing exact input/profile hashes, effective full profile provenance, runtime
source fingerprint, runner digest, artifact hashes and measured stage durations.
The saved canonical program and effective profile must match the loaded source
semantics. Source or implementation changes during the run invalidate acceptance.

Defaults are seed 1, one variant, three candidate attempts, five seconds per room
solve, deterministic work budget 0.25 and the monolithic room solver. The full
pipeline duration includes in-process CLI generation, independent persisted QA
and strict IFC EXPRESS; it excludes Python process startup. No geometry or
clearance thresholds are weakened to obtain acceptance.

On 2026-10-12 the durable local run in `output/checkpoints/domain-pilots` passed in
17.359 seconds: laboratory 3.594 s, hospital 6.781 s and industrial 6.875 s. All
nine persisted artifact checks passed for each pilot and every IFC had zero
strict EXPRESS statements. The unchanged runtime fingerprint was
`80f6c08668ddd60a3634cfee724bebb2cc500280947916e8f0aefc747ac06e0e`.
This identifies that tested runtime; it does not promise identical geometry on
another operating system or dependency set.
The [raw report](../research/domain_pilots/2026_10_12/windows.json) pins the measured
then-working runtime; root independently verified all source/profile and thirty
artifact hashes after the run. Final source-input checks also passed separately
at root (two tests, eight subtests). It is local evidence, not a relabelled clean
remote-CI result.

Fresh related verification passed 40 tests and 75 subtests in 33.01 seconds. The
new tests cover every missing/retyped category, every declared incompatible pair,
all six wrong-profile combinations and union-of-types probes that isolate each
domain's different policy on the same accepted geometry. They also reject
noncanonical coordinates, missing zone type, invalid zone membership, missing
rule provenance, malformed YAML, invalid runner limits and an absent report in
the actual saved bundle. Nine Poppler previews were visually inspected: each
main plan, first functional-zone sheet and report identity page.

Existing rule packs are unchanged. Their declared domain scope is category
completeness and incompatible intermediate-room use. They contain no zone-field
or type policy, pressure cascade, classification, airlock or process-sequence
rule. Invalid zone type/membership is a canonical program boundary rejection,
not a claimed domain rule failure. Broader sourced policies and external
professional, clinical, biosafety, machinery and legal review remain open.
Engineering acceptance remains **PENDING** in the persisted client reports.

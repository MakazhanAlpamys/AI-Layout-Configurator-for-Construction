# Checked conflicts in a selected finite model

`finite_conflicts.explain_finite_conflict` checks a captured CP-SAT model and
its builder-provided `NamedConstraint` registry. Each requirement names actual
terminal clause indices, source JSON pointers and semantic IDs. The complete
capture binds the canonical diagnostic source, candidate family and current
runtime's exact model proto before pruning, support hints and enumeration.
Only boolean, linear and allowed-table terminals support guarded deletion.
Other original constraints and source-dependent generated candidates stay fixed.

The checker clones the model and removes its objective. Single-worker fresh
solves check the base, all requirements, the extracted core, deletion trials,
the final core and every final deletion. `EXPLAINED` requires a final UNSAT
core and a complete SAT witness for each final deletion. This establishes a
subset-minimal conflict relative to the captured base and family. It does not
establish a smallest-cardinality conflict. The original proto stays unchanged.

`NO_CONFLICT` records a feasible captured model. A conflicting unnamed base,
stale identity, incomplete capture, unsafe registry, unsupported enforcement,
missing witness, invalid model or exhausted shared work/time budget produces
`UNKNOWN`. Unfinished searches retain their actual evidence and cannot acquire
the subset-minimal flag. Counts and source/model byte limits apply before clone.

The generic API relies on the actual builder to register the source terminal
clauses. Hashes identify captured inputs; they do not establish arbitrary
caller-provided model/source equivalence. Original-program infeasibility remains
false, full facility validation `UNKNOWN`, and engineering acceptance `PENDING`.

The actual four-room regression has two incompatible contacts in a reversed
two-container family. Eight fresh solves certify both contacts and two final
deletion witnesses; independent coordinate checks confirm envelope nonoverlap
and each remaining full-width contact. Adding a legal shape yields `NO_CONFLICT`
with the exact same canonical source.

## Actual hierarchy registration

C.5.2 appends `explain_conflicts=False` and `conflict_budget=None` to
`solve_zone_layouts`. Explicit true mode registers the actual builder's required
and forbidden room/zone terminal clauses, each fixed room's four equalities and
each selected structural-axis table. Semantic pair IDs use canonical JSON pairs;
all original reverse-declaration pointers are retained, including zone names
whose display strings would otherwise collide.

The captured clone follows the original objective and precedes contact pruning,
translation supports, hints and enumeration exclusions. Evidence distinguishes
the original building, stage projection and diagnostic source-envelope hashes.
The full effective candidate family and checked registry bind the exact model.
Generated shapes may already depend on source constraints: requirement deletion
keeps that family and all unnamed geometry clauses fixed.

Only the initial production model's actual INFEASIBLE outcome triggers conflict
solves. UNKNOWN and later enumeration exhaustion do not. One wall deadline,
solve allowance and deterministic work budget cover all subtree retries and
the final model. The deadline begins at the first snapshot, charges earlier
source/registry preparation and includes intervening production search. Limits
are 32 captures and 32 MiB combined captured bytes. Incomplete, unbound, unsafe
or publicly mutated capture records remain UNKNOWN.

Fresh root verification passed 48 core/registry/delivery/dispatch/stage tests in
8.684 s. An independent fresh actual-builder proof checked the original proto,
all original variable domains, literal room coordinates, nonoverlap, containment,
minima and both final contact-deletion witnesses. Eight fresh solves certify the
two conflicting contacts; an expanded family of the identical source is feasible.
Fixed-versus-axis conflicts work in both hierarchy modes. Shared three-solve
retry budgets stop with incomplete UNKNOWN, and captured hints/no-goods remain
absent. Pinned `4604a10`, omitted, false and true controls retain all eleven
actual production model hashes and the same result in the agent's parity probe.
Original source infeasibility and external engineering acceptance remain separate.

## Public generation and saved CLI evidence

C.5.3 adds strict default-false `explain_zone_conflicts` and optional
`zone_conflict_budget` to `solve_feasible_facility_variants`. They require
`room_solver="zone_hierarchical"` or `"zone_recursive"`. A custom budget requires
explicit true mode. Omitted/false controls forward no additional solver arguments
and preserve serialized generation evidence. The production budget is unchanged.

```console
layout-configurator generate-building program.json --room-solver zone_recursive --explain-zone-conflicts --output out/explained
```

The flag applies to direct generation. Unsupported ordinary backends and an
incremental request with this flag are rejected before source/output mutation.
HTTP controls are supplied by C.5.4 below; supervised controls by C.5.5.
Editor integration remains separate.

Actual reports are retained under
`generation.room_solver_evidence.conflict_explanations`: in `manifest.json` for
an accepted set, or `generation-failure.json` when generation returns exit 2.
Each explanation identifies its capture and the initial production status.
An unnamed base conflict remains UNKNOWN; a checked finite core cannot change
the outer bounded generation failure into proof of whole-source infeasibility.
A feasible result can retain a COMPLETE snapshot with zero diagnostic solves.

Meaningful RED exposed the missing public controls and CLI flag. Fresh root
verification passed 42 related tests in 4.005 s. Durable actual CLI probes
preserved source bytes, retained an initial global base conflict as UNKNOWN,
and produced an accepted three-format bundle whose nine saved-file QA checks
passed independently. Complete pinned `70ee96e` CI subsequently passed in
QA.21; later HTTP controls are outside that pin.

## Persistent HTTP diagnostics

C.5.4 accepts the same strict flag in direct zone requests, with a bounded
JSON `zone_conflict_budget`. Validation and atomic batches reject unsupported
backends, incremental mode, wrong types or budgets before any job is queued.
Omitted/false settings retain the original request/cache identity; enabled
budgets normalize their defaults and are copied into JSON-only persistent
settings. The worker reconstructs `ConflictBudget` only for the direct call.

Actual accepted manifests retain the captured diagnostics; opted generation
failures retain the canonical source and full generation evidence. Restart
reads the same completed job JSON, and successful reuse still checks actual
artifact hashes and independent saved QA. Diagnostic budgets are part of the
cache key. Production constraints and their search budgets are unchanged.

Meaningful missing-contract RED produced four failures in five tests without
errors. Fresh root API/incremental/delivery verification passed 30 tests in
25.250 s; the final six HTTP tests passed in 1.867 s. Durable actual HTTP
accepted/failed jobs preserve JSON settings and source-bound reports after
restart; the accepted bundle passes all nine fresh saved QA checks before a
real cache hit. Its COMPLETE capture uses zero diagnostic solves. The actual
unnamed base conflict remains UNKNOWN, without subset-minimal or original-source
infeasibility claims. See [API.md](API.md) for the request contract.

## Supervised recursive runner controls

C.5.5 exposes `--zone-explain-conflicts` and optional
`--zone-conflict-budget-json` in `scripts/scale80_warmstart_acceptance.py` and
the existing single manual `warmstart80.yml` job. Defaults are false/empty;
omitted or explicit false adds no diagnostic kwargs/settings or solver imports.
The budget needs explicit true mode and the recursive backend. Nonempty JSON
is bounded to1,024 UTF-8 bytes and accepts only `wall_seconds`, `max_solves`
and `deterministic_units`, with existing limits of10 seconds,512 solves and
10 deterministic units (or null). Actual numeric types are preserved;
duplicates, booleans, unknown keys, empty objects, nonfinite and unsafe values
reject as INPUT_REJECTED before starting a worker. The parent forwards the
effective budget as quoted canonical JSON without shell evaluation.

One diagnostic allowance spans the invocation from its first pre-pruning
snapshot, including intervening production. Its deadline is never reset to
force an explanation. Only actual initial INFEASIBLE snapshots trigger
diagnostic search; accepted snapshots, UNKNOWN, MODEL_INVALID and enumeration
results do not. An invocation that eventually succeeds may retain explanations
for earlier failed subtree snapshots. Exhaustion remains UNKNOWN. The outer
generation result and original-source proof boundary are unchanged.

Fresh root verification passed10 tests in18.238 s and actionlint. A real
three-room triangle yields a checked three-requirement subset-minimal core
and three native SAT final-deletion witnesses. Actual budget exhaustion
preserves ROOM_SEARCH_FAILED with UNKNOWN diagnostic evidence. A complete
accepted four-room bundle passes fresh independent saved QA and EXPRESS with
zero diagnostic solves. Actual omitted/false reports and controls match pinned
`9632549`; production proto/result parity remains checked. Detailed local
evidence is in `out/checkpoints/supervised-conflicts-c55/`.

No automatic job, full80 run or new acceptance claim accompanies these controls.
A long physical80 preparation may consume the entire shared deadline before
the final global failure; bounded UNKNOWN is the correct result in that case.

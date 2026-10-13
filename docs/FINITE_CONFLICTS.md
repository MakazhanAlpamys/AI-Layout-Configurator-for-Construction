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
Public delivery is checkpoint C.5.3; original source infeasibility and external
engineering acceptance remain separate.

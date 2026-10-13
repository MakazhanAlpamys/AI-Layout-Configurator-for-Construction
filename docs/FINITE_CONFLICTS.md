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
with the exact same canonical source. Production hierarchy registration,
invocation-wide budgets and persisted failure integration are the next C.5 gate.

# CI execution policy

Checkpoint pushes to main run one Ubuntu job: compilation, collection of every
test (including import errors), and fast canonical/source/geometry contracts.
Documentation-only pushes do not start it. This is explicitly a quick check,
not full facility acceptance. Local affected checks still precede each commit.

Full Windows/Linux suites, the installed private container and the unchanged
three-variant/three-seed pharma matrix run on pull requests, manual dispatch
and weekly on Wednesday at 01:37 UTC. Quick and full runs use separate
concurrency groups: checkpoint pushes cancel stale quick runs without cancelling
a manually pinned full acceptance. A newer full run supersedes an older one.

The complete 20/30/40-room and two-floor scale matrices retain all gates and
seeds. They run weekly on Sunday at 02:17 UTC or on demand. Solver calibration
is manual. These schedules avoid launching six scale jobs every night or
restarting four heavy acceptance jobs after every checkpoint.

Before a release or a completed milestone, dispatch full acceptance and any
relevant scale/calibration workflow against the checkpoint to be reviewed:

```powershell
gh workflow run ci.yml --ref main
gh workflow run scale.yml --ref main
gh workflow run solver-calibration.yml --ref main
```

Evidence always records its tested commit. A green quick job does not replace
the full acceptance record or external engineering review.

The first sharded run at `d4edf62` passed both draft-bundle shards, API and
pharma. Core failed: an IFC fixture released its native file before traversing
borrowed entities, and the Windows log also recorded timestamp-dependent
default-report parity. The native failure reproduced locally on IfcOpenShell
0.9.0; distinct real STEP timestamps deterministically reproduced both group
and grid parity failures. QA.17 retains each file owner during traversal,
checks each actual file SHA, and compares separate exports' exact DATA and
semantic reports while keeping same-file omitted/False report equality.
Fresh group/grid verification passed all 25 tests on 0.9.0; four affected
fixtures also passed on 0.8.5. Full acceptance subsequently passed at `c0eb166`.
[Negative run evidence](acceptance/d4edf62.json) records completed jobs and
the interrupted Windows core without inventing a completed test count.

QA.18 bounds two pilot test setups with 0.25 deterministic work units. All
nine dossier/budget tests and their original 32 assertions remain; equipment
repeatability still performs two real seed-42 packings. The actual width-prepared
pilot now passes fresh equipment, flow and facility checks before review caching
and after each repeated packing. The previous raw dossier setup had unresolved
flows. Fresh root execution took 5.08 s for both modules. Earlier remote log
spans were 60.106 s and 30.741 s on another runtime; this is a setup/workload
change, and its full remote timing remains a separate measurement.

QA.19 retains the complete successful `c0eb166` run: each OS has 1,113 unique
cases split exhaustively into 1,060 core and 53 bundle cases. Linux records four
skips and Windows eight, with all reasons retained. All six active jobs passed;
API and nine pharma variants remain independently verified. Each of six archive
SHA/byte identities and all 84 extracted files match; 54 variant files and three
shared manifests additionally match saved QA inventories. Total workflow elapsed
was 792 s; slowest Windows core job was 787 s including setup. Parallel test wall
times are recorded separately. This does not measure the later QA.18 fixture
optimization or subsequent solver/program changes. [Evidence](acceptance/c0eb166.json).

# CI execution policy

Checkpoint pushes to main run one Ubuntu job: compilation, collection of every
test (including import errors), and fast canonical/source/geometry contracts.
Documentation-only pushes do not start it. This is explicitly a quick check,
not full facility acceptance. Local affected checks still precede each commit.

QA.22 runs the four quick contract files sequentially, with exactly one file
per pytest invocation. The file list, complete collection, shard partition,
workflow triggers, concurrency and full acceptance jobs remain unchanged.
Actionlint and a structural comparison against `8289365` pass. No test suite
or full CI was dispatched to verify this shell-only change; its local evidence
is `out/checkpoints/ci-single-file-qa22/proof.json`. This is an execution-policy
change, without a new timing or memory measurement.

The subsequent [quick run at 4691d38](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38455661550)
passes collection, exhaustive partition and all four single-file invocations.
Its only active job lasts 43 seconds including setup; all full acceptance jobs
are skipped. Queue time is separate. This result does not establish full
acceptance for later changes or an isolated performance effect of QA.22.

During the current memory-constrained development session, local checks also
run one affected test file at a time. Full suites, benchmarks and background
scripts remain suspended until the user explicitly authorizes them again.

Full Windows/Linux suites, the installed private container and the unchanged
three-variant/three-seed pharma matrix run on pull requests, manual dispatch
and weekly on Wednesday at 01:37 UTC. Quick and full runs use separate
concurrency groups: checkpoint pushes cancel stale quick runs without cancelling
a manually pinned full acceptance. A newer full run supersedes an older one.

The complete 20/30/40-room and two-floor scale matrices retain all gates and
seeds. They run weekly on Sunday at 02:17 UTC or on demand. Solver calibration
is manual. These schedules avoid launching six scale jobs every night or
restarting four heavy acceptance jobs after every checkpoint.

When full runs are authorized, before a release or completed milestone dispatch
full acceptance and any relevant scale/calibration workflow against the
checkpoint to be reviewed:

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

QA.21 measures the changed complete milestone at `70ee96e`: workflow elapsed
416 s (6 min 56 s), compared with 792 s (13 min 12 s) at `c0eb166`, a measured
47.5% decrease. All 1,202 unique cases per OS are retained, including all earlier
1,113 IDs. The exact partition is 1,071 core, 53 bundles and 78 exchange/CLI;
skip reasons remain four on Linux and eight on Windows. All eight active jobs
pass, including installed Docker/API and nine pharma variants. Eight downloaded
archive digests and 86 extracted file bytes match. [Evidence](acceptance/70ee96e.json).

The slowest job is Windows exchange/CLI at 411 s including setup. Actual test
walls are Linux229.404/268.398/289.708 s and Windows287.667/320.880/321.446 s
for core/bundles/exchange. Summing these separate VMs is not workflow elapsed.
The change uses six test VMs instead of four and includes different source,
89 added tests and bounded pilot fixtures; runner load is uncontrolled. This
measurement establishes the observed shorter wait, not causal attribution or
a guaranteed reduction in aggregate billed runner minutes. Later source edits
are outside this pinned acceptance.

QA.23 repairs an obsolete test expectation exposed by the already completed
scheduled `34abb9e` run. Both core jobs report exactly one failure: the workflow
test still expects adjacent filenames in the old multi-file pytest command.
It now parses the quick pytest commands, requires one file per invocation,
sequential execution and exactly the same four contract files. The workflow
and full acceptance gates are unchanged. Fresh single-file verification passes
10 tests and 15 subtests in 4.28 s; no full rerun is started.
[Pinned negative evidence](acceptance/34abb9e-ci-failure.json) retains both log
identities and all completed job outcomes. Changed full acceptance is pending.

The subsequent `4217633` push has successful quick CI:
[run 38540041353](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38540041353).
Its one quick job takes 44 seconds including setup; queue time is separate.
Complete collection/partition and four sequential single-file gates pass.
Full jobs are skipped for this push, so it is not full acceptance evidence.

Quick pushes for the QA.23 repair (`54af4e4`,
[run 38540735504](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38540735504))
and I.5.2 (`0286843`,
[run 38542708253](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38542708253))
also pass. The I.5.2 quick job takes 41 seconds including setup, with full jobs
skipped. Collection includes the new documentation tests; quick CI does not
execute their local 14-test artifact/readback verification or replace full acceptance.

The QA.24 `7c10594` push also passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38584729033)
in forty seconds including setup. Complete collection/partition and four
sequential contract files pass; full acceptance jobs are skipped.

The I.4.3.2 `357ddce` source push passes
[quick CI](https://github.com/MakazhanAlpamys/AI-Layout-Configurator-for-Construction/actions/runs/38590587537)
in thirty-eight seconds including setup. All full jobs are skipped; scoped
push checks remain separate from the pending changed full acceptance.

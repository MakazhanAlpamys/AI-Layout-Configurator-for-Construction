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

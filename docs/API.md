# Local facility API

The service runs the same canonical `BuildingIR`, profile, solver, exporters
and independent bundle QA as the CLI. One worker processes persistent jobs in
sequence. Run one writing service per job directory; an OS lock enforces that.

```powershell
$env:LAYOUT_API_TOKEN = 'choose-a-private-token'
.venv\Scripts\python.exe -m layout_configurator.cli api --output out\api-jobs --port 8766
```

Loopback binding is the default. A non-loopback bind requires the token, which
comes from the environment and is never printed. Send
`Authorization: Bearer <token>` and `Content-Type: application/json`.

## Requests

| Method and path | Behavior |
| --- | --- |
| `GET /` | Browser facility-program form and static assets; this public bootstrap contains no jobs or token. |
| `GET /program/config` | Authenticated canonical field schemas and packaged profile choices. |
| `POST /program/validate` | Strict raw input/profile validation and canonical program identity; does not enqueue a job. |
| `GET /health` | Runtime fingerprint and readiness. |
| `POST /jobs` | Validate a canonical program and optional canonical profile, persist the request and queue a solve. Returns 202 and a job ID. |
| `GET /jobs` | List persisted job states. |
| `GET /jobs/{id}` | QUEUED, RUNNING, SUCCEEDED, FAILED, CANCEL_REQUESTED, CANCELLED or INTERRUPTED, plus evidence. |
| `POST /jobs/batch` | Validate an entire `{"jobs": [...]}` batch before enqueueing any request; at most 32 requests. |
| `POST /jobs/{id}/cancel` | Empty JSON object. Cancel queued work, or prevent publication after a running solver call finishes. |
| `POST /jobs/{id}/check` | Empty JSON object. Independently re-read and check every accepted variant and exchange artifact. |
| `GET /jobs/{id}/artifacts` | Names of accepted bundle artifacts. Failed and cancelled jobs do not publish bundles. |
| `GET /jobs/{id}/artifacts/{name}` | Download a listed artifact; hashes and directory boundaries are checked. Review filenames may contain `review/`. |
| `GET /jobs/{id}/review` | Three role dossiers and the artifact inventory for each variant. Expert acceptance remains PENDING. |

`POST /jobs` accepts `program`, optional `profile`, and `variants`,
`max_attempts`, `time_limit_seconds`, `seed`, `equipment_retries`,
`deterministic_units`, `room_solver`, `reuse` and optional `revision_history`.
The history contains exact self-contained prior source JSON and hashes,
never server filesystem paths; see [DRAWING_SHEETS.md](DRAWING_SHEETS.md).
Invalid history is rejected before enqueueing, including in batch requests.
Programs use the canonical
schema, including the normalized fields emitted by `BuildingIR.to_dict()`;
shorthand YAML must be normalized before submission. Unknown fields, paths,
coordinates, invalid settings and non-finite numbers are rejected before work
is queued. Defaults match the facility generator: one variant, 60 seconds per
solver invocation, seed 42, two equipment retries and automatic room backend.

## Browser program entry

Open `http://127.0.0.1:8766/` after starting the service. Enter the service token
when configured, then fill the blank program controls: rooms, zones, equipment,
flows, openings, cutouts and optional structural axes. Reorder or remove source
rows with their visible controls. The form stores the token only in browser
memory; configuration, validation, jobs and downloads retain API authentication.

Choose the default or packaged pharma rule profile and validate the program.
Raw nested schemas reject unknown fields, coerced numbers/booleans and invalid
domain references. JSON requests reject duplicate keys. Validation displays a
read-only canonical preview, its SHA256 and structured field errors; download
returns the canonical program. Any source edit invalidates the previous preview
and disables generation until validation succeeds again.

Generation submits the validated source through the existing persistent job
pipeline. The page displays actual job status, independent gate evidence and
authenticated artifact downloads. VALID INPUT confirms the source contract;
accepted software QA and engineering acceptance remain separate.

The full 13-room pilot was entered through visible browser controls and compared
with direct generation in the same Windows runtime using seed 1, one variant,
three attempts and .25 deterministic units per solver invocation. Canonical and
prepared input, rooms, wall openings, equipment, route points/topology and all
540 unique IFC root identities matched; both complete bundle QA and strict IFC
EXPRESS checks passed. Both BCF packages contained zero topics, so this sample
does not prove positive issue-topic identity. Timestamps, job paths and PDF bytes
are excluded; this is not a cross-platform geometry guarantee.

The opt-in `test_program_form_browser.py` reproduces actual browser entry and
generation via the pinned Playwright CLI. Set `FLC_PLAYWRIGHT_CLI` to its installed
executable and `FLC_BROWSER_PILOT_GENERATION=1` to include full-pilot generation;
`FLC_BROWSER_ARTIFACT_DIR` retains bundles, screenshots and runtime evidence.

Successful jobs retain JSON/DXF/PDF/IFC/BCF, a manifest, QA report and review
dossiers. Failed jobs retain their error and available candidate rejection
evidence in `generation-failure.json`. A solver budget failure does not prove
mathematical impossibility. Restart preserves completed jobs and marks any
unfinished job INTERRUPTED; resubmit it explicitly.

Generation failures also retain structured `failure_evidence` for a room-search
stop and `stage_evidence` on rejected candidates. Actual room/equipment/flow
predicates keep their issue codes and source identities; failed facility checks
keep their saved status, evidence and rule provenance. Every unsuccessful
equipment retry records its seed and search scope. Generic bounded-search errors
remain UNKNOWN, while independently failed candidate predicates remain FAIL.
These records explicitly set `proven_building_infeasible: false`; they do not
claim a minimal conflicting constraint set or whole-program impossibility.

## Cache

`reuse: true` requires `deterministic_units`. It reuses an accepted job only
when canonical program, profile, settings, executable source, schemas and
dependency versions match. Every artifact hash and the independent bundle QA
are checked before reuse. A hit returns HTTP 200 and `cache_hit: true`; changed
inputs or artifacts cause a fresh job. Wall-clock searches are not cached.

## Private container

```powershell
$env:LAYOUT_API_TOKEN = 'choose-a-private-token'
docker compose up --build -d
```

The image installs a regular wheel with schemas, rule packs and IDS, includes
examples, runs as an
unprivileged user, and stores jobs in the `layout-jobs` volume. The host port is
bound to loopback. `docker compose down` stops the service and preserves its
volume; use one service instance per volume. This is a private single-user
service, without multi-tenant accounts or hosted deployment.

`scripts/api_smoke.py` submits the canonical pharma pilot to a running service,
downloads its PDF and rechecks its full bundle and three unsigned dossiers.
Set `LAYOUT_API_TOKEN` and pass `--url` for a non-default port. CI builds the
image and runs this check, retaining its JSON evidence and service logs.

Software QA and dossiers do not close process/QA, HVAC or receiving CAD/BIM
reviews. Their decision blocks remain external acceptance conditions.

Non-editable wheels also include the built-in schema/rule/IDS resources.
After installation, CLI defaults work from another directory. Existing local
`schemas/`, `rules/` or `ids/` files override relative defaults; explicit
absolute paths retain their meaning, including errors for missing files.

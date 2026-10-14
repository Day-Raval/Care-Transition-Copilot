# Local deployment

This profile runs the built React app and FastAPI on one loopback address, with the
existing local Cox model, Chroma retrieval, SQLite decisions, and file-based state.
Groq handles new AI drafting, critique, and chat. This is the synthetic-data MVP.

## Install on Windows

Prerequisites: Windows, `uv`, and Node.js/npm. The verified versions are Python
3.12.13 and Node 24.19.0. WSL is not required. Python's exact direct/transitive
versions are in `requirements.lock`; frontend versions are in `web/package-lock.json`.
The installer creates `.venv-local` and uses `uv pip sync` and `npm ci`.

From the repository root:

```powershell
Copy-Item .env.example .env  # only if .env does not already exist
# Edit .env: set API_KEY to a stable random value, and set GROQ_API_KEY.
# Optional PATIENT_REF_SALT must also remain stable; otherwise API_KEY is used.
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command install
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command check
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command run
```

Generate a local key with `uv run --python 3.12 --no-project python -c "import secrets; print(secrets.token_hex(32))"`.
Open **http://127.0.0.1:8080**. Stop with Ctrl+C. If the port is occupied, the launcher
refuses to kill its owner; choose `-Port 8088` for both `check` and `run` instead.

The launcher selects local persistence, in-memory caching, disabled external
notifications, disabled Kafka, disabled tracing, and manual precomputation without
editing the existing `.env`. Existing Postgres records remain in Postgres; this
profile uses `results/care_delivery` for its own local decisions and plans.
API-key authentication is for this loopback synthetic demo. The browser receives
its demo key at runtime; Groq/database credentials are never included in this
configuration. This also keeps browser authentication working after restoring a
backup with a different local key.

## Provision a clean machine

A source checkout alone is insufficient: the trained model and vector store are
ignored by Git. Copy a **verified deployment backup** to the new machine, install
dependencies, restore it, then run `check` before `run`. Installation/building can
complete before `.env` is restored. The required assets are:

- `config.yaml` and its configured `models/<run_id>.joblib`.
- `data/processed/discharge_records_with_target.csv` and discharge notes.
- `data/processed/chroma_db`, containing the nonempty `discharge_notes` collection.
- `.env`, with a stable demo key/reference salt and a valid Groq key for new AI work.

The first retrieval query downloads Chroma's default ONNX embedding model if it is
not cached. Run `check` while internet is available to populate the cache. The
model/CSV/vector store must come from the same provisioned deployment.

For an initial dataset/model build, use the existing [pipeline instructions](../README.md).
Do not retrain merely to start a deployed app; restore its versioned artifact.

## Startup and failures

`check` and server startup validate configuration, artifact loading, nonempty model
data, finite risk scores, a real patient-scoped embedding/query, SQLite integrity
and write access, JSONL readability, report write access, and the built frontend.
Startup fails before accepting browser traffic if a required local dependency fails.

- `/api/ready`: HTTP 200 only after local validation; does not require Groq availability.
- `/api/health`: dependency details and the last observed AI state. `not_checked`
  means no provider call has verified availability yet. A provider failure makes
  health `degraded`; a successful subsequent provider call clears that state.
- AI connection, authentication, timeout, and provider errors return a safe HTTP
  503 with a request ID. Patient browsing, indexed history, saved decisions, saved
  assessments, and saved approved reports remain accessible without new AI calls.
- Reports are saved when approval is recorded and replaced atomically. Existing
  approved reports can be retrieved without regenerating the assessment.

Open **Operations** for health, model/drift status, audit events, and handoff records.
Logs appear in the launch console and rotate in `logs/pipeline.log`. Use the request
ID shown in a browser error to correlate API/audit entries. After changing `.env`,
restart the app; the runtime demo key does not require rebuilding the frontend.

## Backup and restore

Stop **all app processes using this checkout**, including development servers.
Do not run Windows and WSL apps against the same state concurrently. The local
launcher locks its deployment against another local instance and maintenance.

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command backup
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command restore -Archive backups/local-<timestamp>.zip
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command check
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command run
```

Snapshots include `.env`, configuration, models, processed data/vector store,
results (decisions, plans, audit, prediction logs, follow-ups, reminders, notification
and FHIR records), and reports. They exclude source code, frontend builds, embedding
model caches, raw FHIR source files, and dependencies. Keep the matching source
revision and lockfiles separately. The MVP snapshots require standard local paths.

Snapshots contain credentials: keep them in a protected location. Backups use a
temporary archive and atomic replacement. Restore verifies the full file manifest,
checksums, and safe paths before replacing state. It removes stale state by replacing
the snapshot directories, retaining the previous directories under
`backups/before-restore-*`. Restore refuses while the local app holds its lock.

Before an upgrade: stop, back up, update the source, run `install`, run `check`, then
start. For rollback, stop, restore the snapshot and matching source version, reinstall,
check, and start. Avoid upgrading model/retrieval dependencies without compatibility
checks against the saved artifacts.

## Verification

For GitHub Actions and packaged local updates with rollback, see [CI/CD](CI_CD.md).

```powershell
.venv-local/Scripts/python.exe -m pytest -q
cd web
npm ci
npm run build:local
npx playwright install chromium
npm run test:e2e
```

Browser tests generate an isolated synthetic dataset/model/store and use real risk
scoring, retrieval, API routes, and persistent storage. They stub hosted AI responses
and use deterministic test embeddings for success/outage coverage;
test-only controls are absent from the deployed app. The separate-process integration
test verifies approval, assessment, and report persistence after an actual process
exit/restart with Groq calls prohibited. It requires provisioned artifacts and a build.

## Linux / WSL

Use Python 3.12, `uv`, and Node/npm in the Linux environment, then run:

```bash
bash scripts/local.sh install
bash scripts/local.sh check
bash scripts/local.sh run
bash scripts/local.sh backup
bash scripts/local.sh restore --archive backups/local-<timestamp>.zip
```

WSL installs Python dependencies under `~/.cache/care-transition-copilot/local-venv`
to avoid slow Windows-mounted virtual environments. `CARE_TRANSITION_LOCAL_VENV`
overrides that location. Linux uses `.venv-local` by default. Internet connectivity
must work inside the selected runtime for installation and new Groq generations.

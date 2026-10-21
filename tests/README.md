# Verification

Python regressions live here; Chromium workflows live in [web/tests](../web/tests/local.spec.js). Run from the repository root with locked Python dependencies installed.

## Run locally

Windows, after local installation:

```powershell
.venv-local/Scripts/python.exe -m pytest -q tests
cd web
npm ci
npm run build:local
npx playwright install chromium
npm run test:e2e
```

Linux: `.venv-local/bin/python -m pytest -q tests`. WSL's default interpreter is `~/.cache/care-transition-copilot/local-venv/bin/python`; use Linux Node/npm and set `CARE_TRANSITION_TEST_PYTHON` to the interpreter's absolute path before browser tests.

Container tests need no local data or provider keys:

```bash
docker build --target ci -t copilot-ci:local .
docker run --rm --init --shm-size=1g copilot-ci:local
```

## Coverage

| Area | Representative files |
| --- | --- |
| Auth, request IDs, privacy | `test_api_security.py`, `test_request_id.py`, `test_patient_privacy.py` |
| Queue, cache, precompute | `test_patient_queue.py`, `test_assessment_cache.py`, `test_precompute.py` |
| Approval, reports, persistence | `test_decision_idempotency.py`, `test_transition_report.py`, `test_data_services_production.py` |
| Retrieval and chat tools | `test_chunking.py`, `test_chat_tools.py` |
| Events, notifications, metrics | `test_kafka_consumer.py`, `test_notifications.py`, `test_observability.py` |
| Deployment, restore, release, rollback | `test_local_deployment.py`, `test_release.py` |
| Reads across separate API processes | `test_local_restart.py` |

`browser_fixtures.py` builds temporary synthetic data, a fitted Cox model, and a Chroma collection. `web_server.py` serves the application with deterministic AI and embedding fixtures, including outage controls. Test controls are excluded from the runtime image.

The separate process-restart test requires provisioned assets and skips when absent. Browser workflows verify real APIs, scoring, retrieval, approval, and saved state; fixture responses do not validate live Groq availability, generated-text clinical quality, or model calibration. A runtime import check also does not establish readiness with deployment assets.

CI adds secret scanning, Linux container tests, and native Windows tests; see [automation](../.github/AUTOMATION.md). Historical counts in the main README describe their dated revision, not a fresh test run.

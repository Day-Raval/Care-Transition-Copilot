# Clinician workspace

React 18 + Vite frontend for the [Care Transition Copilot MVP](../README.md).

| Screen | Purpose |
| --- | --- |
| Risk queue | Filter discharges, inspect risk/evidence, approve or reject plans |
| Ask a question | Patient-name-based chart and risk questions |
| Patients | Search patients and inspect history |
| Care plans | Revisit saved plans and review state |
| Operations | Inspect dependencies, audit events, and handoff records |

`src/App.jsx` defines navigation; `src/components/` contains screens. `src/api.js` handles requests, `AuthContext.jsx` and `oidc.js` handle auth, and the other helpers format names, care plans, and Markdown.

## Development

Prepare Python dependencies and assets using the [backend guide](../src/README.md). Configure root `.env` with `VITE_API_KEY` matching `API_KEY` for API-key development. From `web/`:

```bash
npm ci
npm run dev:full
```

This starts API and Vite on Linux/WSL. On Windows, `npm run dev:full:win` uses WSL. For native Windows without WSL, use [local deployment](../docs/LOCAL_DEPLOYMENT.md).

`npm run dev` starts only Vite at `http://127.0.0.1:5173` and expects a separately running API. The proxy target defaults to `http://127.0.0.1:8080`, overridable with `API_PROXY_TARGET`; `/api` is stripped before forwarding. OIDC settings are in [`.env.example`](../.env.example).

## Builds and browser tests

```bash
npm run build
npm run build:local
npx playwright install chromium
npm run test:e2e
```

`build` creates a development-configured bundle. `build:local` targets the one-address local deployment: `/api`, API-key mode, and runtime configuration from `/local-config.js`. Never place provider/database secrets in `VITE_*` values; browser configuration is public.

Playwright starts a Python fixture server on port 8093 with temporary synthetic assets and deterministic AI/embedding responses. It needs the local build and locked Python dependencies. Set `CARE_TRANSITION_TEST_PYTHON` to an absolute interpreter path if it is outside `.venv-local`. See [test coverage and limits](../tests/README.md).

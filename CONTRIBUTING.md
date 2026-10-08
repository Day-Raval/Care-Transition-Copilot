# Contributing

Thanks for improving Care Transition Copilot. Keep changes small, tested, and
safe for a synthetic healthcare AI demo.

## Ground Rules

- Use synthetic data only. Do not commit real patient data, PHI, secrets, tokens,
  screenshots containing sensitive data, or private logs.
- Keep generated clinical text visibly advisory and clinician-reviewed.
- Update tests when changing auth, patient references, persistence, retrieval,
  model behavior, or care-plan decision logic.
- Prefer existing project patterns over new frameworks or abstractions.
- Document demo-only stubs honestly instead of presenting them as production
  integrations.

## Local Setup

```bash
cp .env.example .env
uv venv
uv pip install -r requirements.txt
uv pip install -e .
cd web
npm install
```

You can also use the uv project workflow:

```bash
uv sync --dev
```

## Checks

Run the same checks as CI before opening a pull request:

```bash
bash scripts/check.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

## Pull Requests

Include:

- What changed and why.
- Any setup, migration, or environment variable changes.
- Test results.
- Known limitations or demo-only behavior.

Security issues should not be opened publicly. See `SECURITY.md`.

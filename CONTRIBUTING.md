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
test -f .env || cp .env.example .env
uv venv --python 3.12
uv pip sync --python .venv/bin/python requirements.lock
source .venv/bin/activate
cd web
npm ci
```

On native Windows, from the repository root:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
uv venv --python 3.12
uv pip sync --python .venv/Scripts/python.exe requirements.lock
. .venv/Scripts/Activate.ps1
cd web
npm ci
```

Edit `.env` for your synthetic demo. Run Python modules from the repository root;
an editable package install is not required. See [backend setup](src/README.md)
for data/model provisioning and development launchers, or
[local deployment](docs/LOCAL_DEPLOYMENT.md) for the built application.

### Dependency maintenance

- `pyproject.toml` is the source for direct runtime dependencies and the `dev`
  group. Edit it first when adding or removing a dependency.
- `requirements.txt` is the compatibility mirror used by existing development
  bootstrapping scripts. Keep its entries equal to the runtime dependencies plus
  the `dev` group in the same change.
- `requirements.lock` pins the validated runtime and test dependencies. CI,
  containers, local deployment, and contributor setup install this file.
  Do not regenerate it for documentation or unrelated code changes.

When dependencies intentionally change, refresh the lock in the validated
Python 3.12 Linux/WSL environment, review the version changes, and check model and
retrieval compatibility before committing all affected dependency files:

```bash
uv pip compile requirements.txt --python-version 3.12 --output-file requirements.lock
uv pip sync --python .venv/bin/python requirements.lock
```

Run Python and browser checks on the supported Windows and Linux/container
profiles before releasing a dependency update. Avoid `uv sync` or an unlocked
install for verification: they resolve independently of `requirements.lock`.

## Checks

From the repository root with the environment above activated, run the Python
tests and frontend build before opening a pull request:

```bash
bash scripts/check.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check.ps1
```

These scripts are a quick check, not the complete CI pipeline. Run the browser
suite as described in [tests/README.md](tests/README.md); CI also runs secret
scanning and container checks.

## Pull Requests

Include:

- What changed and why.
- Any setup, migration, or environment variable changes.
- Test results.
- Known limitations or demo-only behavior.

Security issues should not be opened publicly. See `SECURITY.md`.

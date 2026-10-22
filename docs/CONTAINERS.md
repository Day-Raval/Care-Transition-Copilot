# Containers, GHCR, and the next deployment step

## What is published

The existing release ZIP and checksums stay on GitHub Releases. Version-tag pushes
also publish two Linux AMD64 images:

| Image | Purpose |
| --- | --- |
| `ghcr.io/day-raval/care-transition-copilot` | Built browser UI and Python API, running as UID/GID 10001 |
| `ghcr.io/day-raval/care-transition-copilot-ci` | Source, locked dependencies, Chromium, Python/browser tests, ZIP packaging |

Both receive the exact Git version tag (for example `v0.2.0`) and
`sha-<full-commit>`. Stable version tags also update `latest`; prereleases do not.
BuildKit generates SBOM and provenance attestations for published images. Image
digests appear in Actions job summaries. Deploy by digest for exact rollback.
Manual Release runs build previews without publishing images or releases.

The application image excludes tests and their provider stubs/control endpoints.
Neither image contains your `.env`, datasets, trained models, vector store, saved
decisions, reports, or backups. The build context uses an allowlist. The CI image
contains only `.env.example`, not live credentials.

Linux tests and ZIP packaging run inside the CI image. Windows tests stay native
because Linux containers cannot verify Windows file locks, launchers, and paths.
Git checkout, the secret scanner, artifact upload, and registry/release operations
remain GitHub Actions steps; they do not need separate project images.

## Build or reproduce CI locally

Start Docker Desktop with the Linux engine (or use Docker Engine on Linux):

```bash
docker build --target ci -t copilot-ci:local .
docker run --rm --init --shm-size=1g copilot-ci:local
docker build --target runtime -t copilot:local .
```

The CI image's default command runs Python tests followed by browser tests. Tests
generate temporary synthetic assets and deterministic AI responses; no API keys
or local datasets are required. The runtime import check does not replace a
startup/readiness check against provisioned model and retrieval assets.

## Run the application

Use a dedicated deployment directory containing `compose.yml`, a configured
`.env`, `config.yaml`, provisioned `data/` and `models/`, plus writable `results/`,
`reports/`, `logs/`, and `backups/` directories. Follow LOCAL_DEPLOYMENT.md for asset
provisioning and stable API/patient-reference keys. On Linux, grant UID/GID 10001
read access to model/configuration files and write access to state directories.
The Chroma data directory must be writable even when serving existing records.
The embedding cache volume may download its embedding model on first startup.

Stop the native app before starting the container: both use the same state and
deployment lock. From the repository or deployment directory:

```bash
# Set an existing published version; use a digest for deployed environments.
export COPILOT_IMAGE=ghcr.io/day-raval/care-transition-copilot:v0.2.0
docker compose pull
docker compose up -d --no-build
docker compose ps
curl --fail http://127.0.0.1:8080/api/ready
docker compose logs --tail=100 copilot
```

In PowerShell use `$env:COPILOT_IMAGE = 'ghcr.io/day-raval/care-transition-copilot:v0.2.0'`
instead of `export`. To build unpublished local changes, run
`docker compose up -d --build` instead of pulling.

The host port is bound to loopback. This profile serves the existing local UI,
which delivers its API key to the browser through `/local-config.js`. Keep it on
a trusted single-user machine; remote/multi-user hosting needs a separate auth
and TLS design. Missing or incompatible assets fail startup rather than serving
partial results. A Docker health check reports readiness; `restart: unless-stopped`
restarts exited processes, but does not restart an unhealthy running process.

## Publish the first version

Merge the reviewed changes into `main`, run a manual Release preview, and inspect
container tests and the ZIP artifact. Then push a new version tag as described in
CI_CD.md. GitHub Actions authenticates to GHCR with `GITHUB_TOKEN`; no personal
access token is needed for workflow publishing. Ensure repository policy permits
package writes. New packages can be private: choose public visibility in package
settings if anonymous pulls are intended, or authenticate deployments with
read-package access. Existing packages must grant this repository Actions access.

Do not move a release tag to retry changed source. A failed partial publish can
leave one image in GHCR without a GitHub Release; inspect both matrix jobs and
rerun the failed job for the same commit. Concurrent stable releases should be
published in version order because `latest` tracks the last successful push.

## Run-history review (2026-10-21)

Reviewed all 29 available runs and their job results: 25 succeeded and 4 failed.
Two runs were GitHub-managed Copilot activities rather than project workflows.

| Runs | Finding |
| --- | --- |
| [38606537007](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/38606537007) | Latest CI: secret scan, Linux and Windows tests passed |
| [38603770642](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/38603770642) | Latest Release preview passed; publication intentionally skipped |
| [38603220188](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/38603220188), [38603305089](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/38603305089) | Windows setup failed because Python 3.12.13 was unavailable; current workflows use the subsequently validated 3.12.10 |
| [37830793175](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/37830793175) | npm rejected the old React plugin/Vite peer dependency mismatch; later runs passed after the dependency fix |
| [37831173070](https://github.com/Day-Raval/Care-Transition-Copilot/actions/runs/37831173070) | Failed PR run has no retained jobs in the API; exact failure cannot be confirmed from logs |

There are no tagged release runs in this history. Historical green checks do not
validate this new container publishing path; a preview and first tagged run are
still required.

## Beyond CI: recommended order

1. Deploy one versioned image to a synthetic-data staging machine. Check actual
   startup, readiness, patient workflows, model compatibility and retrieval.
2. Add a manual CD workflow using a GitHub `staging` environment. Deploy the
   selected digest, check readiness, and restore the previous digest on failure.
   Select the host and access mechanism before implementing this workflow.
3. Rehearse state backup and restore before promoting a version. Image rollback
   alone cannot undo changed database/vector-store formats. Existing ZIP updater
   commands manage native installations; replace the image for container updates.
   Existing snapshots expect `.env` as a file, while containers receive environment
   variables: arrange a protected host-side credentials backup before relying on
   container snapshots for full recovery.
4. Add image vulnerability scanning and a patch/rebuild policy, then connect
   existing metrics to alerts for readiness failures and provider outages.
5. Before remote or multi-user hosting, design TLS, per-user authentication,
   authorization and protected state storage. Keep this project on synthetic data
   until its separate clinical/privacy requirements have been addressed.

References: [GitHub image publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images)
and [GHCR access and visibility](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

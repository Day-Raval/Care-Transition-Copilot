# Launchers and utilities

Run from the repository root unless noted. Scripts use the configuration and generated assets in that checkout.

| Script | Use |
| --- | --- |
| `local.ps1` / `local.sh` | Windows or Linux/WSL install, check, run, backup, restore, update |
| `local.py` | Deployment validation, locking, and state snapshots |
| `release.py` | Checksummed application packaging and update/rollback |
| `start_dev.sh` / `start_dev.ps1` | API/Vite development; Windows development uses WSL |
| `check.sh` / `check.ps1` | Python tests and frontend development build; not the complete CI pipeline |
| `generate_synthetic_data.sh` | Synthea FHIR/note generation using configured population and seed |
| `export_records.py` | Export discharge records and notes |
| `precompute_assessments.py` | Precompute assessments using the API |
| `run_kafka_consumer.py` | Run the configured event consumer |
| `check_*`, `compare_*`, `find_*`, `rank_*`, `test_scoped_retrieval.py` | Focused data/retrieval diagnostics; inspect expected assets and arguments first |

## Local deployment

```powershell
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command check
powershell -ExecutionPolicy Bypass -File scripts/local.ps1 -Command run
```

```bash
bash scripts/local.sh check
bash scripts/local.sh run
```

Use `install` first on a new environment. Native Windows/Linux use `.venv-local`; WSL defaults to `~/.cache/care-transition-copilot/local-venv`. See [deployment](../Docs/LOCAL_DEPLOYMENT.md) for provisioning and environment overrides.

Stop app/development processes before restoring or updating. Backups contain credentials and state: protect them and keep them out of Git. Release ZIPs contain code and a built UI, not datasets, model artifacts, or deployment credentials. Updates verify checksums, save recovery copies, synchronize dependencies, validate readiness, and roll back on failure. The update command leaves the app stopped.

Synthea generation deletes old raw FHIR/note/metadata output. Preserve needed assets first. Container updates replace the image rather than using the native ZIP updater.

See [backup/restore](../Docs/LOCAL_DEPLOYMENT.md), [releases and updates](../Docs/CI_CD.md), [containers](../Docs/CONTAINERS.md), and the [synthetic pipeline](../src/README.md#build-the-synthetic-pipeline).

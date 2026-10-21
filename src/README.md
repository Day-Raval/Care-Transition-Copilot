# Backend and synthetic pipeline

[Project overview](../README.md) · [Architecture](../ARCHITECTURE.md)

Run Python modules from the repository root. `config.yaml` selects data paths, episode rules, feature windows, and the production model run. `.env` supplies runtime credentials and integration settings.

| Package | Responsibility | Entry points |
| --- | --- | --- |
| `ingestion/` | Synthetic FHIR/HL7 parsing, temporal filters, discharge episodes | `fhir_parser.py`, `hl7_intake.py`, `episodes.py` |
| `features/` | Construct the 30-day readmission target | `target.py` |
| `model/` | Survival-model training, comparison, registry, fairness | `run_experiment.py`, `experiment_registry.py`, `fairness_audit.py` |
| `embeddings/` | Section-aware chunking and Chroma build/validation | `chunking.py`, `build_vector_store.py`, `validate_vector_store.py` |
| `retrieval/` | Patient-scoped note queries | `query_store.py` |
| `agents/` | Risk tools, retrieval, drafting, critique, chat | `orchestrator.py`, `chat_agent.py`, `tools.py` |
| `api/` | Routes, auth, persistence, reports, cache, precompute, metrics | `main.py`, `local.py`, `production.py`, `security.py` |
| `data_services/` | Kafka, idempotency, notifications | `kafka_events.py`, `consumer.py`, `notifications.py` |
| `utils/` | Configuration, logging, runtime errors | `config.py`, `logging_config.py`, `runtime.py` |

The local API serves the built UI and mounts routes at `/api`. The standalone development API omits that prefix; Vite strips it before forwarding. Low-risk cases receive a short summary; medium/high-risk cases run retrieval, drafting, and critique. Clinician approval remains a separate action.

## Build the synthetic pipeline

For an existing deployment, restore matching artifacts instead of retraining. The Synthea generator clears existing `data/raw/fhir`, `data/raw/notes`, and `data/raw/metadata` output before generation. Preserve needed data first. Keep model, feature CSV, notes, and vector-store artifacts matched.


The development server remains available separately at `http://127.0.0.1:5173`.
Use a development Python environment and install the locked dependencies:

```bash
uv venv --python 3.12
uv pip sync requirements.lock
```

Set `VITE_API_KEY` to match `API_KEY` for the development frontend. To build a
first synthetic dataset/model, activate the environment and run:

```bash
source .venv/bin/activate
bash scripts/generate_synthetic_data.sh
python scripts/export_records.py
python -m src.features.target
python -m src.model.run_experiment --alpha 1.0 --notes "baseline, 6 features"
# Set model.production_run_id in config.yaml to the run ID printed above.
python -m src.embeddings.build_vector_store
python -m src.embeddings.validate_vector_store
```

For PowerShell Python commands, activate with `.venv\Scripts\Activate.ps1`.
Synthea generation uses the shell script and Java. Stop the local deployment
before starting development servers. Use `bash dev.sh` on Linux/WSL or
`npm run dev:full:win` from `web` on Windows (the
Windows development launcher uses WSL). Use the [native local launcher](../Docs/LOCAL_DEPLOYMENT.md) for
a deployment without WSL. WSL's development launcher uses a separate environment
at `~/.cache/care-transition-copilot/venv` unless `CARE_TRANSITION_VENV` points
to the Linux environment you prepared. Standalone development API routes omit the `/api`
prefix, for example `http://127.0.0.1:8080/health`.

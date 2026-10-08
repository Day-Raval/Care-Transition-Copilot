# Observability

Start the API first:

```powershell
uvicorn src.api.main:app --port 8080
```

Then start Prometheus and Grafana:

```powershell
docker compose -f docker-compose.observability.yml up
```

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000
- Grafana login: `admin` / `admin`

Check the API scrape endpoint with:

```powershell
curl http://localhost:8080/metrics/
```

Enable LangSmith tracing with redacted inputs:

```powershell
$env:LANGSMITH_TRACING = "true"
$env:LANGSMITH_API_KEY = "<your LangSmith key>"
$env:LANGSMITH_PROJECT = "care-transition-copilot-dev"
```

Prometheus labels intentionally avoid patient names, patient IDs, prompts, chart text, and model outputs.

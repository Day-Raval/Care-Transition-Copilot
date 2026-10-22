# syntax=docker/dockerfile:1
FROM node:24.19.0-bookworm-slim@sha256:a9f5f7c91a432850b2a8a7797adf5eadb6c733ceed61167806cee7ea7fbc29df AS frontend
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build:local

FROM python:3.12.10-slim-bookworm@sha256:fd95fa221297a88e1cf49c55ec1828edd7c5a428187e67b5d1805692d11588db AS dependencies
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONUTF8=1 \
    PYTHON_DOTENV_DISABLED=1 PYTHONPATH=/app
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.3@sha256:90bbb3c16635e9627f49eec6539f956d70746c409209041800a0280b93152823 /uv /usr/local/bin/uv
COPY requirements.lock requirements.txt pyproject.toml README.md LICENSE ./
RUN uv pip sync --system --no-cache requirements.lock
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY --from=frontend /app/web/dist ./web/dist

FROM dependencies AS ci
COPY --from=frontend /usr/local/bin/node /usr/local/bin/node
COPY --from=frontend /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY web/ ./web/
COPY tests/ ./tests/
COPY config.yaml ./config.yaml
COPY .env.example ./.env.example
COPY docs/CI_CD.md docs/LOCAL_DEPLOYMENT.md docs/CONTAINERS.md ./docs/
RUN cd web && npm ci && npx playwright install --with-deps chromium
ENV CARE_TRANSITION_TEST_PYTHON=/usr/local/bin/python CI=1
CMD ["sh", "-c", "python -m pytest -q tests --junitxml=test-results/python.xml && npm --prefix web run test:e2e"]

FROM dependencies AS runtime
LABEL org.opencontainers.image.source="https://github.com/Day-Raval/Care-Transition-Copilot" \
    org.opencontainers.image.licenses="MIT"
RUN groupadd --gid 10001 copilot && useradd --uid 10001 --gid copilot --create-home copilot \
    && mkdir -p data models results reports logs backups \
    && chown -R copilot:copilot /app
USER 10001:10001
ENV PERSISTENCE_BACKEND=local AUTH_MODE=api_key RISK_API_BASE_URL=http://127.0.0.1:8080/api \
    KAFKA_ENABLED=false KAFKA_CONSUMER_ENABLED=false LANGSMITH_TRACING=false \
    NOTIFICATIONS_ENABLED=false PRECOMPUTE_ON_STARTUP=false REDIS_URL=""
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/ready', timeout=4)"
CMD ["python", "-m", "uvicorn", "src.api.local:create_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]

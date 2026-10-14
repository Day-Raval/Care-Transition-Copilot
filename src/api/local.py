"""Single-machine deployment: one origin, built SPA, validated local state."""
from contextlib import asynccontextmanager, contextmanager, closing
import os
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "web" / "dist"


@contextmanager
def deployment_lock(root=ROOT):
    lock_path = Path(root) / "logs" / "local.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
        else:
            import fcntl
        try:
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Local app or maintenance is running. Stop it before continuing.") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def validate_configuration():
    from src.utils.config import load_config
    cfg = load_config()
    if os.getenv("PERSISTENCE_BACKEND", "local") != "local":
        raise RuntimeError("Local deployment requires PERSISTENCE_BACKEND=local.")
    if os.getenv("AUTH_MODE", "api_key") != "api_key":
        raise RuntimeError("Local deployment requires AUTH_MODE=api_key.")
    for name in ("API_KEY", "PATIENT_REF_SALT"):
        # Preserve existing references: the API already falls back to the stable API key.
        value = (os.getenv(name) or os.getenv("API_KEY", "")) if name == "PATIENT_REF_SALT" else os.getenv(name, "")
        if len(value) < 16 or value.startswith("replace_"):
            raise RuntimeError(f"Set {name} to a stable random value of at least 16 characters in .env.")
    # This deployment owns its local files; external services belong to other profiles.
    for name in ("KAFKA_ENABLED", "KAFKA_CONSUMER_ENABLED", "LANGSMITH_TRACING", "NOTIFICATIONS_ENABLED", "PRECOMPUTE_ON_STARTUP"):
        if os.getenv(name, "false").lower() in {"true", "1", "yes", "on"}:
            raise RuntimeError(f"Set {name}=false for the single-computer deployment.")
    if os.getenv("REDIS_URL", "").strip():
        raise RuntimeError("Leave REDIS_URL empty for the single-computer deployment.")
    if (cfg.output_csv != "data/processed/discharge_records.csv"
        or cfg.output_notes != "data/processed/discharge_notes.jsonl"
        or os.getenv("CHROMA_PATH", "data/processed/chroma_db") != "data/processed/chroma_db"):
        raise RuntimeError("Local deployment requires the standard data/processed paths for complete snapshots.")
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]+", cfg.production_run_id):
        raise RuntimeError("Set a valid model.production_run_id in config.yaml.")
    if not cfg.production_run_id or not (ROOT / "models" / f"{cfg.production_run_id}.joblib").is_file():
        raise RuntimeError("Configured model artifact is missing; restore a deployment backup or run model training.")
    if not (DIST / "index.html").is_file():
        raise RuntimeError("Built frontend is missing. Run scripts/local.ps1 -Command install (or scripts/local.sh install).")
    return cfg


def validate_data_and_storage():
    from src.api import main, production
    from src.retrieval.query_store import get_collection
    import numpy as np
    if main._state["queue_df"].empty or not np.isfinite(main._state["reference_scores"]).all():
        raise RuntimeError("Modeling data must contain scorable patient episodes with finite risk scores.")
    collection = get_collection()
    if collection.count() == 0:
        raise RuntimeError("Retrieval collection is empty. Build or restore the vector store.")
    # Exercise embedding and querying; a directory/count check misses broken model caches/indexes.
    sample = collection.get(limit=1, include=["documents", "metadatas"])
    if not sample["documents"] or not sample["metadatas"][0].get("patient_id"):
        raise RuntimeError("Retrieval data is missing documents or patient metadata.")
    result = collection.query(
        query_texts=["discharge follow-up"], n_results=1,
        where={"patient_id": sample["metadatas"][0]["patient_id"]},
    )
    if not result["ids"][0]:
        raise RuntimeError("Patient-scoped retrieval startup query returned no results.")
    with closing(sqlite3.connect(production.DECISIONS_DB_PATH)) as conn, conn:
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RuntimeError("Decision database integrity check failed. Restore a backup.")
        conn.execute("BEGIN IMMEDIATE")
        conn.rollback()
    for directory in (production.CARE_DELIVERY_RESULTS_DIR, production.OPERATIONS_RESULTS_DIR, production.REPORTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=directory) as handle:
            handle.write(b"startup storage check")
            handle.flush()
            os.fsync(handle.fileno())
    for path in (production.CARE_PLANS_PATH, production.AUDIT_LOG_PATH, production.FOLLOW_UPS_PATH,
                 production.REMINDERS_PATH, production.NOTIFICATIONS_PATH, production.FHIR_WRITEBACKS_PATH):
        production.read_jsonl(path, limit=1)
    return {"configuration": True, "model": True, "patient_data": True,
            "retrieval_query": True, "persistent_storage": True, "frontend": True}


def create_app():
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse, Response
    from fastapi.staticfiles import StaticFiles
    from src.api import main

    @asynccontextmanager
    async def lifespan(app):
        with deployment_lock():
            validate_configuration()
            async with main.app.router.lifespan_context(main.app):
                main._state["local_readiness"] = validate_data_and_storage()
                main.logger.info("Local startup validation complete; browser app and API ready")
                try:
                    yield
                finally:
                    main._state.pop("local_readiness", None)

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/api", main.app)
    app.mount("/assets", StaticFiles(directory=DIST / "assets", check_dir=False))

    @app.get("/local-config.js")
    def runtime_config():
        import json
        config = json.dumps({"apiKey": os.environ["API_KEY"]}).replace("<", "\\u003c")
        return Response(f"globalThis.__LOCAL_CONFIG__ = {config};", media_type="application/javascript",
                        headers={"Cache-Control": "no-store", "Cross-Origin-Resource-Policy": "same-origin",
                                 "X-Content-Type-Options": "nosniff"})

    @app.get("/{path:path}")
    def frontend(path: str):
        # Only actual browser routes receive the SPA; typos/assets must return 404.
        if path not in {"", "chat", "patients", "care-plans", "operations"}:
            raise HTTPException(status_code=404, detail="Not found")
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})

    return app

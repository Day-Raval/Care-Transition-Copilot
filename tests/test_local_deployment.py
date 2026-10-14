import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch
import zipfile

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from groq import APIConnectionError

from scripts.local import create_backup, restore_backup
from src.api import local, main, production
from src.utils.runtime import LLMUnavailableError


def test_lock_excludes_another_instance_and_maintenance(tmp_path):
    with local.deployment_lock(tmp_path):
        with pytest.raises(RuntimeError, match="running"):
            with local.deployment_lock(tmp_path):
                pass
    with local.deployment_lock(tmp_path):
        pass


def test_snapshot_restores_exact_state_and_retains_previous_state(tmp_path):
    (tmp_path / ".env").write_text("API_KEY=stable-key\n")
    (tmp_path / "config.yaml").write_text("model: {production_run_id: test}\n")
    for name in ("models/test.joblib", "data/processed/discharge_records_with_target.csv",
                 "data/processed/chroma_db/chroma.sqlite3", "reports/approved.md"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("original")
    archive = create_backup(tmp_path, tmp_path / "backups/test.zip")
    (tmp_path / "reports/approved.md").write_text("changed")
    (tmp_path / "reports/stale.md").write_text("stale")
    recovery = restore_backup(tmp_path, archive)
    assert (tmp_path / "reports/approved.md").read_text() == "original"
    assert not (tmp_path / "reports/stale.md").exists()
    assert (recovery / "reports/approved.md").read_text() == "changed"
    assert (tmp_path / ".env").read_text() == "API_KEY=stable-key\n"


@pytest.mark.parametrize("unsafe", ["../outside", "/outside", "reports/../../outside", "reports\\..\\outside"])
def test_restore_rejects_unsafe_paths_before_changing_state(tmp_path, unsafe):
    (tmp_path / ".env").write_text("unchanged")
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(unsafe, "payload")
        z.writestr("manifest.json", json.dumps({"format": 1, "files": {unsafe: "bad"}}))
    with pytest.raises(RuntimeError):
        restore_backup(tmp_path, archive)
    assert (tmp_path / ".env").read_text() == "unchanged"


def test_corrupt_backup_does_not_replace_files(tmp_path):
    (tmp_path / ".env").write_text("unchanged")
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(".env", "corrupt")
        z.writestr("manifest.json", json.dumps({"format": 1, "files": {".env": "bad"}}))
    with pytest.raises(RuntimeError):
        restore_backup(tmp_path, archive)
    assert (tmp_path / ".env").read_text() == "unchanged"


def test_readiness_does_not_accept_partially_loaded_state():
    with patch.dict(main._state, {"model": object()}, clear=True):
        response = TestClient(main.app).get("/ready")
        assert response.status_code == 503


def test_startup_rejects_a_nonempty_but_unqueryable_vector_store():
    collection = SimpleNamespace(
        count=lambda: 10,
        get=lambda **kw: {"documents": ["note"], "metadatas": [{"patient_id": "p"}]},
        query=lambda **kw: {"ids": [[]]},
    )
    with patch.dict(main._state, {"queue_df": pd.DataFrame([{"patient_id": "p"}]),
                                 "reference_scores": np.array([1.])}, clear=True), \
         patch("src.retrieval.query_store.get_collection", return_value=collection):
        with pytest.raises(RuntimeError, match="returned no results"):
            local.validate_data_and_storage()


def test_ai_outage_is_safe_503_and_does_not_block_other_routes():
    with patch.dict(os.environ, {"AUTH_MODE": "api_key", "API_KEY": "test-key"}), \
         patch.dict(main._state, {"run_id": "test"}, clear=True), \
         patch("src.agents.chat_agent.ask", side_effect=APIConnectionError(request=httpx.Request("POST", "https://api.groq.com"))), \
         patch.object(main, "_chat_fast_path", return_value=None), \
         patch.object(main, "read_care_plans", return_value=[]):
        client = TestClient(main.app)
        response = client.post("/chat", json={"question": "Explain this workflow"}, headers={"X-API-Key": "test-key"})
        assert response.status_code == 503
        assert response.json()["detail"] == LLMUnavailableError.MESSAGE
        assert response.headers["x-request-id"]
        assert client.get("/care-plans", headers={"X-API-Key": "test-key"}).status_code == 200


def test_saved_report_is_available_without_regeneration(tmp_path):
    with patch.dict(main._state, {"run_id": "test"}, clear=True), \
         patch.object(main, "_resolve_patient_key", return_value="p"), \
         patch.object(main, "_latest_discharge_ts", return_value="2026-01-01"), \
         patch.object(main, "latest_decision", return_value={"decision": "approved"}), \
         patch.object(production, "REPORTS_DIR", tmp_path), \
         patch.object(main, "_generate_assessment", side_effect=AssertionError("must not call AI")):
        production.save_transition_report("p", "2026-01-01", "Approved saved plan")
        assert main.patient_report("p").report_markdown == "Approved saved plan"


def test_built_spa_routes_and_mounted_api_auth(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<html>local app</html>")
    with patch.object(local, "DIST", tmp_path), \
         patch.dict(os.environ, {"AUTH_MODE": "api_key", "API_KEY": "test-key"}), \
         patch.object(main, "read_care_plans", return_value=[]):
        client = TestClient(local.create_app())
        for path in ("/", "/patients", "/care-plans", "/operations", "/chat"):
            assert client.get(path).status_code == 200
        assert client.get("/api/care-plans").status_code == 401
        assert client.get("/api/care-plans", headers={"X-API-Key": "test-key"}).status_code == 200
        assert client.get("/.env").status_code == 404
        assert client.get("/api/not-an-endpoint", headers={"X-API-Key": "test-key"}).status_code == 404
        assert client.get("/assets/missing.js").status_code == 404
        config = client.get("/local-config.js")
        assert config.headers["cache-control"] == "no-store"
        assert config.headers["cross-origin-resource-policy"] == "same-origin"
        assert "test-key" in config.text
        with patch.dict(os.environ, {"API_KEY": "restored-key"}):
            assert "restored-key" in client.get("/local-config.js").text

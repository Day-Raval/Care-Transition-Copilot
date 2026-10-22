from unittest.mock import ANY, MagicMock
from pathlib import Path
import subprocess
import sys

from tests import browser_fixtures


def test_browser_fixture_uses_configured_chroma_path(tmp_path, monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    persistent_client = MagicMock(return_value=client)
    monkeypatch.setattr(browser_fixtures, "CHROMA_PATH", "custom/vector_store")
    monkeypatch.setattr(browser_fixtures, "COLLECTION_NAME", "custom_notes")
    monkeypatch.setattr(browser_fixtures.chromadb, "PersistentClient", persistent_client)

    browser_fixtures.prepare_browser_fixture(tmp_path)

    persistent_client.assert_called_once_with(path=str(tmp_path / "custom/vector_store"))
    client.create_collection.assert_called_once_with(
        "custom_notes", embedding_function=ANY)
    assert len(client.create_collection.return_value.add.call_args.kwargs["ids"]) == 450
    client.__exit__.assert_called_once()


def test_browser_fixture_queries_every_patient_before_and_after_process_restart(tmp_path):
    program = r'''
from pathlib import Path
import sys
import chromadb
from tests.browser_fixtures import prepare_browser_fixture, CHROMA_PATH, COLLECTION_NAME
root = Path(sys.argv[1])
if sys.argv[2] == 'create':
    prepare_browser_fixture(root)
with chromadb.PersistentClient(path=str(root / CHROMA_PATH)) as client:
    collection = client.get_collection(COLLECTION_NAME)
    assert collection.count() == 450
    for index in range(150):
        patient_id = f'synthetic-patient-{index}'
        result = collection.query(query_texts=['discharge follow-up'], n_results=3,
                                  where={'patient_id': patient_id})
        assert len(result['ids'][0]) == 3, (patient_id, result['ids'])
        assert all(item['patient_id'] == patient_id for item in result['metadatas'][0])
'''
    for phase in ("create", "reopen"):
        result = subprocess.run([sys.executable, "-c", program, str(tmp_path), phase],
                                cwd=Path(__file__).resolve().parents[1],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, f"{phase}: {result.stdout}\n{result.stderr}"

from unittest.mock import ANY, MagicMock

from tests import browser_fixtures


def test_browser_fixture_uses_configured_chroma_path(tmp_path, monkeypatch):
    client = MagicMock()
    persistent_client = MagicMock(return_value=client)
    monkeypatch.setattr(browser_fixtures, "CHROMA_PATH", "custom/vector_store")
    monkeypatch.setattr(browser_fixtures, "COLLECTION_NAME", "custom_notes")
    monkeypatch.setattr(browser_fixtures.chromadb, "PersistentClient", persistent_client)

    browser_fixtures.prepare_browser_fixture(tmp_path)

    persistent_client.assert_called_once_with(path=str(tmp_path / "custom/vector_store"))
    client.create_collection.assert_called_once_with(
        "custom_notes", embedding_function=ANY)
    assert len(client.create_collection.return_value.add.call_args.kwargs["ids"]) == 450

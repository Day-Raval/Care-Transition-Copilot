from tests import browser_fixtures


def test_browser_fixture_uses_configured_chroma_path(tmp_path, monkeypatch):
    paths = []
    monkeypatch.chdir(tmp_path)

    class Collection:
        def add(self, **kwargs):
            pass

    class Client:
        def create_collection(self, name, embedding_function):
            assert name == "discharge_notes"
            return Collection()

    def persistent_client(**kwargs):
        paths.append(kwargs["path"])
        return Client()

    monkeypatch.setattr(browser_fixtures.chromadb, "PersistentClient", persistent_client)
    browser_fixtures.prepare_browser_fixture(tmp_path)

    assert paths == ["data/processed/chroma_db"]

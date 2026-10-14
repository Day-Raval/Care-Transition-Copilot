import hashlib
import json
from unittest.mock import patch
import zipfile

import pytest

from scripts import release


def application(root, text="new"):
    for name in ("scripts/local.py", "src/api/local.py", "requirements.lock", "web/dist/index.html"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def state(root):
    (root / ".env").write_text("API_KEY=private-local-value", encoding="utf-8")
    (root / "config.yaml").write_text("model: {production_run_id: existing}", encoding="utf-8")
    for name in ("models/existing.joblib", "data/processed/discharge_records_with_target.csv",
                 "data/processed/chroma_db/chroma.sqlite3", "reports/approved.md"):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("saved state", encoding="utf-8")


def package(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    application(source)
    output = tmp_path / "output"
    archive = release.build_package(source, output, "v0.2.0", "a" * 40)
    return archive, output / "SHA256SUMS"


def test_release_contains_only_application_and_checksums(tmp_path):
    application(tmp_path)
    state(tmp_path)
    for name in (".env.local", "scripts/__pycache__/secret.pyc", "scripts/download.jar", "backups/private.zip", "web/node_modules/private.txt"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private", encoding="utf-8")
    archive = release.build_package(tmp_path, tmp_path / "output", "v1.2.3", "a" * 40)
    with zipfile.ZipFile(archive) as z:
        names = set(z.namelist())
        assert names == {"scripts/local.py", "src/api/local.py", "requirements.lock", "web/dist/index.html", "release.json"}
    staging = tmp_path / "staging"
    staging.mkdir()
    assert release.verify_package(archive, tmp_path / "output/SHA256SUMS", staging)["version"] == "v1.2.3"


def test_update_preserves_state_and_keeps_recovery_copy(tmp_path):
    archive, checksums = package(tmp_path)
    target = tmp_path / "installed"
    target.mkdir()
    application(target, "old")
    state(target)
    with patch.object(release, "sync_dependencies") as sync, patch.object(release, "check_readiness") as check:
        manifest, recovery = release.apply_release(target, archive, checksums)
    assert manifest["version"] == "v0.2.0"
    assert (target / "src/api/local.py").read_text() == "new"
    assert (target / ".env").read_text() == "API_KEY=private-local-value"
    assert (target / "reports/approved.md").read_text() == "saved state"
    assert (recovery / "code/src/api/local.py").read_text() == "old"
    assert (recovery / "state.zip").exists()
    sync.assert_called_once()
    check.assert_called_once()


def test_failed_readiness_rolls_back_code_dependencies_and_state(tmp_path):
    archive, checksums = package(tmp_path)
    target = tmp_path / "installed"
    target.mkdir()
    application(target, "old")
    state(target)
    def check(root, python):
        if (root / "src/api/local.py").read_text() == "new":
            (root / "reports/approved.md").write_text("changed during failed startup")
            raise RuntimeError("model incompatible")
    with patch.object(release, "sync_dependencies") as sync, patch.object(release, "check_readiness", side_effect=check):
        with pytest.raises(RuntimeError, match="previous code, dependencies, and state restored"):
            release.apply_release(target, archive, checksums)
    assert (target / "src/api/local.py").read_text() == "old"
    assert (target / "reports/approved.md").read_text() == "saved state"
    assert sync.call_count == 2


@pytest.mark.parametrize("name", ["../outside.py", "src/../../outside.py", ".env", "config.yaml", "results/state.jsonl", "models/model.joblib", "src\\outside.py"])
def test_release_rejects_unsafe_and_state_paths(tmp_path, name):
    archive, checksums = package(tmp_path)
    with zipfile.ZipFile(archive) as original:
        contents = {entry: original.read(entry) for entry in original.namelist()}
    manifest = json.loads(contents["release.json"])
    manifest["files"][name] = hashlib.sha256(b"bad").hexdigest()
    contents[name] = b"bad"
    contents["release.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(archive, "w") as edited:
        for entry, data in contents.items():
            edited.writestr(entry, data)
    checksums.write_text(f"{release.digest(archive)}  {archive.name}\n", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(RuntimeError):
        release.verify_package(archive, checksums, staging)


def test_modified_archive_rejected_before_update(tmp_path):
    archive, checksums = package(tmp_path)
    with archive.open("ab") as handle:
        handle.write(b"tampered")
    staging = tmp_path / "staging"
    staging.mkdir()
    with pytest.raises(RuntimeError, match="archive checksum"):
        release.verify_package(archive, checksums, staging)

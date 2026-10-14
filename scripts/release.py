"""Build versioned application packages and apply them locally with rollback."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

from src.api.local import ROOT, deployment_lock

FILES = {"README.md", "LICENSE", "pyproject.toml", "requirements.txt", "requirements.lock", ".env.example",
         "Docs/LOCAL_DEPLOYMENT.md", "Docs/CI_CD.md", "web/package.json", "web/package-lock.json",
         "web/vite.config.js", "web/index.html"}
DIRECTORIES = ("src", "scripts", "web/src", "web/public", "web/dist")
VERSION = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?")


def application_path(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and name == path.as_posix() and "\\" not in name
            and not any(part in {"..", ".git", "node_modules", "__pycache__"} for part in path.parts)
            and (not path.name.startswith(".env") or name == ".env.example")
            and path.suffix not in {".pyc", ".pyo", ".jar", ".zip"}
            and (name in FILES or any(name.startswith(directory + "/") for directory in DIRECTORIES)))


def application_files(root):
    candidates = [root / name for name in FILES]
    for directory in DIRECTORIES:
        folder = root / directory
        if folder.is_symlink():
            raise RuntimeError("Application directories must not be symlinks.")
        if folder.is_dir():
            candidates.extend(folder.rglob("*"))
    files = []
    for path in candidates:
        if path.is_symlink():
            raise RuntimeError("Application packages must not contain symlinks.")
        if path.is_file() and application_path(path.relative_to(root).as_posix()):
            if not path.resolve().is_relative_to(root):
                raise RuntimeError("Application file escaped the project directory.")
            files.append(path)
    return sorted(files)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def build_package(root, output, version, commit):
    root, output = Path(root).resolve(), Path(output).resolve()
    if not VERSION.fullmatch(version) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise RuntimeError("Use a version such as v0.2.0 and a full Git commit SHA.")
    for required in ("web/dist/index.html", "requirements.lock", "scripts/local.py", "src/api/local.py"):
        if not (root / required).is_file():
            raise RuntimeError(f"Build is missing required application file: {required}")
    output.mkdir(parents=True, exist_ok=True)
    target = output / f"care-transition-copilot-{version}.zip"
    if target.exists():
        raise RuntimeError("Release package already exists; choose a new output directory.")
    manifest = {"format": 1, "version": version, "commit": commit, "files": {}}
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in application_files(root):
            name = path.relative_to(root).as_posix()
            archive.write(path, name)
            manifest["files"][name] = digest(path)
        archive.writestr("release.json", json.dumps(manifest, indent=2))
    (output / "SHA256SUMS").write_text(f"{digest(target)}  {target.name}\n", encoding="utf-8")
    return target


def verify_package(source, checksums, staging):
    source, staging = Path(source).resolve(), Path(staging).resolve()
    entries = [line.split(maxsplit=1) for line in Path(checksums).read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = [entry[0] for entry in entries if len(entry) == 2 and entry[1].strip().lstrip("*") == source.name]
    if len(expected) != 1 or digest(source) != expected[0]:
        raise RuntimeError("Release archive checksum does not match SHA256SUMS.")
    with zipfile.ZipFile(source) as archive:
        names = archive.namelist()
        manifest = json.loads(archive.read("release.json"))
        if (not isinstance(manifest, dict) or manifest.get("format") != 1
            or not VERSION.fullmatch(str(manifest.get("version", "")))
            or not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("commit", "")))
            or not isinstance(manifest.get("files"), dict) or len(names) != len(set(names))
            or set(names) != set(manifest["files"]) | {"release.json"}):
            raise RuntimeError("Invalid application release manifest.")
        required = {"web/dist/index.html", "requirements.lock", "scripts/local.py", "src/api/local.py"}
        if not required.issubset(manifest["files"]):
            raise RuntimeError("Application release is incomplete.")
        for name, checksum in manifest["files"].items():
            target = (staging / name).resolve()
            if not application_path(name) or not target.is_relative_to(staging):
                raise RuntimeError("Release contains an unsafe or runtime-state path.")
            if archive.getinfo(name).external_attr >> 16 & 0o170000 == 0o120000:
                raise RuntimeError("Release contains a symlink.")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(name) as reader, target.open("wb") as writer:
                shutil.copyfileobj(reader, writer)
            if digest(target) != checksum:
                raise RuntimeError(f"Release file checksum failed: {name}")
    return manifest


def sync_dependencies(root, python):
    subprocess.run(["uv", "pip", "sync", "--python", str(python), str(root / "requirements.lock")], cwd=root, check=True)


def check_readiness(root, python):
    # The updater owns the deployment lock while this child validates the installed app.
    program = (
        "from contextlib import nullcontext; from src.api import local; local.deployment_lock=nullcontext; "
        "from scripts.local import main; import sys; sys.argv=['local','check']; main()"
    )
    subprocess.run([str(python), "-c", program], cwd=root, check=True, timeout=180)


def apply_release(root, source, checksums, python=sys.executable):
    from scripts.local import create_backup, restore_backup
    root = Path(root).resolve()
    with deployment_lock(root):
        backups = root / "backups"
        backups.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="release-staging-", dir=backups) as folder:
            staging = Path(folder).resolve()
            manifest = verify_package(source, checksums, staging)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            recovery = backups / ("update-" + stamp)
            recovery.mkdir(mode=0o700)
            snapshot = create_backup(root, recovery / "state.zip")
            old_files = {path.relative_to(root).as_posix(): path for path in application_files(root)}
            for name, path in old_files.items():
                saved = recovery / "code" / name
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, saved)
            (recovery / "previous-files.json").write_text(json.dumps(sorted(old_files)), encoding="utf-8")
            new_names = set(manifest["files"])
            try:
                for name in old_files.keys() - new_names:
                    old_files[name].unlink()
                for name in new_names:
                    target = root / name
                    if not target.resolve().is_relative_to(root):
                        raise RuntimeError("Application destination escaped the project directory.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(staging / name, target)
                sync_dependencies(root, python)
                check_readiness(root, python)
            except Exception as update_error:
                try:
                    for name in new_names - old_files.keys():
                        (root / name).unlink(missing_ok=True)
                    for name in old_files:
                        target = root / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(recovery / "code" / name, target)
                    restore_backup(root, snapshot)
                    sync_dependencies(root, python)
                    check_readiness(root, python)
                except Exception as rollback_error:
                    raise RuntimeError(f"Update and rollback validation failed. Recovery files are at {recovery}.") from rollback_error
                raise RuntimeError(f"Update failed; previous code, dependencies, and state restored. Recovery: {recovery}") from update_error
    return manifest, recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", default=None)
    args = parser.parse_args()
    commit = args.commit or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    print(build_package(ROOT, args.output, args.version, commit))


if __name__ == "__main__":
    main()

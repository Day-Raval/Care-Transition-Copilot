"""Single-computer deployment, validation, and offline state maintenance."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
import zipfile

from dotenv import load_dotenv

from src.api.local import ROOT, deployment_lock


def snapshot_paths(root):
    # A complete local snapshot includes provisioned data/models and the stable reference salt.
    return [root / name for name in (".env", "config.yaml", "models", "data/processed",
                                     "results", "reports")]


def create_backup(root, destination):
    destination = Path(destination).resolve()
    paths = snapshot_paths(root)
    if destination.exists():
        raise RuntimeError("Backup destination already exists; choose a new filename.")
    if any(destination.is_relative_to(path.resolve()) for path in paths if path.is_dir()):
        raise RuntimeError("Store backups outside the directories being backed up.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"format": 1, "created_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    fd, temp_name = tempfile.mkstemp(dir=destination.parent, suffix=".zip")
    os.close(fd)
    os.chmod(temp_name, 0o600)
    try:
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in paths:
                if path.is_symlink():
                    raise RuntimeError("Snapshot paths must not contain symlinks.")
                files = sorted(path.rglob("*")) if path.is_dir() else [path]
                for file in files:
                    if file.is_symlink():
                        raise RuntimeError("Snapshot paths must not contain symlinks.")
                    if not file.is_file():
                        continue
                    name = file.relative_to(root).as_posix()
                    digest = hashlib.sha256()
                    with file.open("rb") as source, archive.open(name, "w", force_zip64=True) as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            digest.update(chunk)
                            target.write(chunk)
                    manifest["files"][name] = digest.hexdigest()
            archive.writestr("manifest.json", json.dumps(manifest, indent=2))
        os.replace(temp_name, destination)
    finally:
        Path(temp_name).unlink(missing_ok=True)
    return destination


def restore_backup(root, source):
    root = Path(root).resolve()
    allowed = snapshot_paths(root)
    with tempfile.TemporaryDirectory(prefix="copilot-restore-", dir=root) as staging:
        staging = Path(staging)
        with zipfile.ZipFile(source) as archive:
            names = archive.namelist()
            manifest = json.loads(archive.read("manifest.json"))
            if (not isinstance(manifest, dict) or manifest.get("format") != 1
                or not isinstance(manifest.get("files"), dict) or len(names) != len(set(names))):
                raise RuntimeError("Unsupported backup format or duplicate archive entries.")
            if set(names) != set(manifest["files"]) | {"manifest.json"}:
                raise RuntimeError("Backup contents do not match the manifest.")
            for name, expected in manifest["files"].items():
                target = (root / name).resolve()
                if "\\" in name or not target.is_relative_to(root) or not any(
                    target == p or (p.name not in {".env", "config.yaml"} and target.is_relative_to(p))
                    for p in allowed
                ):
                    raise RuntimeError("Backup contains an unsafe path.")
                dest = staging / name
                dest.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                with archive.open(name) as reader, dest.open("wb") as writer:
                    for chunk in iter(lambda: reader.read(1024 * 1024), b""):
                        digest.update(chunk)
                        writer.write(chunk)
                if digest.hexdigest() != expected:
                    raise RuntimeError(f"Backup checksum failed for {name}.")
            # Reject incomplete snapshots before moving any existing state.
            import yaml
            config_file = staging / "config.yaml"
            if not config_file.is_file() or not (staging / ".env").is_file():
                raise RuntimeError("Backup must include configuration and stable local credentials.")
            cfg = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
            if not isinstance(cfg, dict) or not isinstance(cfg.get("model"), dict):
                raise RuntimeError("Backup contains an invalid model configuration.")
            run_id = cfg["model"].get("production_run_id", "")
            import re
            if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
                raise RuntimeError("Backup contains an invalid production model run ID.")
            for required in (f"models/{run_id}.joblib", "data/processed/discharge_records_with_target.csv",
                             "data/processed/chroma_db/chroma.sqlite3"):
                if not (staging / required).is_file():
                    raise RuntimeError(f"Backup is missing required asset: {required}.")
        # Keep originals as a rollback snapshot; never erase current state before verification.
        recovery = root / "backups" / ("before-restore-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        recovery.mkdir(parents=True)
        installed, moved = [], []
        try:
            for target in allowed:
                relative = target.relative_to(root)
                old = recovery / relative
                if target.exists():
                    old.parent.mkdir(parents=True, exist_ok=True)
                    target.rename(old)
                    moved.append((old, target))
                restored = staging / relative
                if restored.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    restored.rename(target)
                    installed.append(target)
        except Exception:
            for path in reversed(installed):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
            for old, target in reversed(moved):
                old.rename(target)
            raise
    return recovery


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run", "check", "backup", "restore", "update"])
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--checksums", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    os.chdir(ROOT)
    load_dotenv(ROOT / ".env")
    # Select the lightweight profile without changing the user's development .env.
    os.environ.update({"PERSISTENCE_BACKEND": "local", "AUTH_MODE": "api_key", "REDIS_URL": "",
                       "KAFKA_ENABLED": "false", "KAFKA_CONSUMER_ENABLED": "false",
                       "LANGSMITH_TRACING": "false", "NOTIFICATIONS_ENABLED": "false",
                       "PRECOMPUTE_ON_STARTUP": "false"})
    try:
        if args.command == "update":
            if not args.archive or not args.checksums:
                raise RuntimeError("Update requires --archive <release.zip> and --checksums <SHA256SUMS>.")
            from scripts.release import apply_release
            release, recovery = apply_release(ROOT, args.archive, args.checksums)
            print(f"Installed {release['version']} ({release['commit'][:7]}). Recovery files: {recovery}")
            print("Readiness passed. Start the app with the local run command.")
            return
        if args.command in {"backup", "restore"}:
            with deployment_lock():
                if os.getenv("PERSISTENCE_BACKEND", "local") != "local":
                    raise RuntimeError("These snapshots only support local persistence.")
                from src.utils.config import load_config
                cfg = load_config()
                if (cfg.output_csv != "data/processed/discharge_records.csv"
                    or cfg.output_notes != "data/processed/discharge_notes.jsonl"
                    or os.getenv("CHROMA_PATH", "data/processed/chroma_db") != "data/processed/chroma_db"):
                    raise RuntimeError("Snapshots require the standard local data paths.")
                if args.command == "backup":
                    name = datetime.now(timezone.utc).strftime("local-%Y%m%dT%H%M%S%fZ.zip")
                    print(f"Backup saved: {create_backup(ROOT, args.archive or ROOT / 'backups' / name)}")
                else:
                    if not args.archive:
                        raise RuntimeError("Restore requires --archive <backup.zip>.")
                    print(f"Restore complete. Previous state retained at: {restore_backup(ROOT, args.archive)}")
            return
        if not 1024 <= args.port <= 65535:
            raise RuntimeError("Choose a local port between 1024 and 65535.")
        os.environ["RISK_API_BASE_URL"] = f"http://127.0.0.1:{args.port}/api"
        if args.command == "check":
            import asyncio
            from src.api.local import create_app
            async def check():
                app = create_app()
                async with app.router.lifespan_context(app):
                    print("Ready: configuration, model, patient data, retrieval, storage, and frontend validated.")
            asyncio.run(check())
        else:
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", args.port))
                except OSError as exc:
                    raise RuntimeError(f"Port {args.port} is in use. Stop its owner or choose --port; no process was killed.") from exc
            import uvicorn
            print(f"Starting local app at http://127.0.0.1:{args.port} (Ctrl+C to stop).", flush=True)
            uvicorn.run("src.api.local:create_app", factory=True, host="127.0.0.1", port=args.port,
                        workers=1, access_log=False)
    except (RuntimeError, OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
        parser.exit(1, f"Local deployment failed: {exc}\n")


if __name__ == "__main__":
    main()

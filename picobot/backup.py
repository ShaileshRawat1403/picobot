"""Portable, credential-free backups of Pico Home state.

The backup boundary is intentionally narrower than the filesystem.  It copies
only Pico-owned durable state from the configured workspace, never the active
configuration, environment files, or runtime credential material.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


_STATE_DIRECTORIES = (
    "artifacts",
    "context",
    "learning",
    "memory",
    "missions",
    "operations",
    "projects",
    "runs",
    "sessions",
    "tasks",
    "workflows",
)
_STATE_FILES = ("AGENTS.md", "HEARTBEAT.md", "SOUL.md", "TOOLS.md", "USER.md")
_SENSITIVE_NAME_PARTS = ("credential", "token", "secret", "password", "api_key", "apikey")


@dataclass(frozen=True)
class PicoHomeBackup:
    """Safe public receipt for one completed archive."""

    path: Path
    files: int
    bytes: int


def create_pico_home_backup(workspace: Path, destination: Path) -> PicoHomeBackup:
    """Archive Pico-owned workbench state without credentials or machine paths."""
    workspace = workspace.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("Pico workspace does not exist")
    if _is_within(destination, workspace):
        raise ValueError("Choose a backup destination outside the Pico workspace")
    destination.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = destination / f"pico-home-{stamp}.zip"
    suffix = 1
    while final_path.exists():
        final_path = destination / f"pico-home-{stamp}-{suffix}.zip"
        suffix += 1

    entries: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="pico-home-backup-") as temp_dir:
        staging = Path(temp_dir)
        for source in _state_sources(workspace):
            relative = source.relative_to(workspace)
            if _skip(source, relative):
                continue
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.suffix == ".db":
                _copy_sqlite_snapshot(source, target)
            else:
                target.write_bytes(source.read_bytes())
            data = target.read_bytes()
            entries.append(
                {
                    "path": relative.as_posix(),
                    "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )

        manifest = {
            "format": "pico-home-backup/v1",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "contents": sorted(entries, key=lambda item: str(item["path"])),
            "excluded": "Pico configuration, environment files, runtime tokens, and known credential filenames are never included.",
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary_archive = destination / f".{final_path.name}.tmp"
        try:
            with zipfile.ZipFile(temporary_archive, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in sorted(staging.rglob("*")):
                    if path.is_file():
                        archive.write(path, path.relative_to(staging).as_posix())
            os.replace(temporary_archive, final_path)
        finally:
            temporary_archive.unlink(missing_ok=True)

    return PicoHomeBackup(path=final_path, files=len(entries), bytes=final_path.stat().st_size)


def _state_sources(workspace: Path):
    for name in _STATE_FILES:
        path = workspace / name
        if path.is_file():
            yield path
    for name in _STATE_DIRECTORIES:
        root = workspace / name
        if not root.is_dir():
            continue
        yield from (path for path in root.rglob("*") if path.is_file())


def _skip(path: Path, relative: Path) -> bool:
    if path.is_symlink() or path.name.endswith(("-wal", "-shm")):
        return True
    lowered = "/".join(part.lower() for part in relative.parts)
    return ".env" in lowered or any(part in lowered for part in _SENSITIVE_NAME_PARTS)


def _copy_sqlite_snapshot(source: Path, target: Path) -> None:
    """Use SQLite's online backup API so WAL databases produce a complete copy."""
    try:
        with sqlite3.connect(source) as source_connection, sqlite3.connect(target) as target_connection:
            source_connection.backup(target_connection)
    except sqlite3.Error as exc:
        raise ValueError(f"Pico state database could not be backed up: {source.name}") from exc


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from picobot.backup import create_pico_home_backup


def test_backup_archives_pico_state_and_uses_consistent_sqlite_snapshot(tmp_path: Path):
    workspace = tmp_path / "workspace"
    db_root = workspace / "memory"
    db_root.mkdir(parents=True)
    database = db_root / "pico-memory.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE entries (value TEXT NOT NULL)")
        connection.execute("INSERT INTO entries VALUES ('durable memory')")
        connection.execute("PRAGMA journal_mode = WAL")
    (workspace / "sessions").mkdir()
    (workspace / "sessions" / "web.jsonl").write_text('{"role":"user"}\n', encoding="utf-8")
    (workspace / "USER.md").write_text("Prefer concise plans.\n", encoding="utf-8")

    result = create_pico_home_backup(workspace, tmp_path / "exports")

    assert result.path.is_file()
    assert result.files == 3
    with zipfile.ZipFile(result.path) as archive:
        assert set(archive.namelist()) == {"USER.md", "manifest.json", "memory/pico-memory.db", "sessions/web.jsonl"}
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["format"] == "pico-home-backup/v1"
        assert str(workspace) not in archive.read("manifest.json").decode("utf-8")
        extracted = tmp_path / "restored-memory.db"
        extracted.write_bytes(archive.read("memory/pico-memory.db"))
    with sqlite3.connect(extracted) as connection:
        assert connection.execute("SELECT value FROM entries").fetchone()[0] == "durable memory"


def test_backup_excludes_env_runtime_and_known_credential_files(tmp_path: Path):
    workspace = tmp_path / "workspace"
    operations = workspace / "operations"
    operations.mkdir(parents=True)
    with sqlite3.connect(operations / "pico-actions.db") as connection:
        connection.execute("CREATE TABLE actions (id TEXT NOT NULL)")
    (operations / "runtime-token.json").write_text("must not export", encoding="utf-8")
    (operations / ".env").write_text("must not export", encoding="utf-8")
    (workspace / "memory").mkdir()
    (workspace / "memory" / "memory.md").write_text("keep this", encoding="utf-8")

    result = create_pico_home_backup(workspace, tmp_path / "exports")

    with zipfile.ZipFile(result.path) as archive:
        names = set(archive.namelist())
    assert "memory/memory.md" in names
    assert "operations/runtime-token.json" not in names
    assert "operations/.env" not in names


def test_backup_refuses_destination_inside_workspace(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="outside the Pico workspace"):
        create_pico_home_backup(workspace, workspace / "exports")

"""Stable owner identity for Pico's durable stores.

Every durable store (memory, projects, artifacts, sessions, tasks, runs,
missions, workflows, learning) filters its rows by ``owner_id``.  Ownership is
scoped to a *channel identity* rather than to a chat, so two people talking to
the same Pico through the same messaging channel never see each other's memory.

That model is correct, but the web workbench originally derived its identity
from a random UUID held in the browser's ``localStorage``.  Clearing site data,
switching browsers, or opening a private window therefore minted a brand new
owner that could not see any previous work.  The rows stayed in SQLite,
permanently invisible, with no recovery path in the interface.

The web workbench and the CLI are not two people.  They are whoever controls
this machine and this workspace, so they share one stable local owner.  Remote
messaging channels keep their own per-sender identity, because a channel can
allowlist more than one human.

Profiles stay isolated without any work here: a different ``--config`` resolves
to a different workspace path, and therefore to a different set of SQLite files.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

#: The single owner shared by every local surface (web workbench and CLI).
LOCAL_OWNER_ID = "local:owner"

#: Channels that are, by definition, the person operating this machine.
LOCAL_CHANNELS = frozenset({"web", "cli"})

#: Owner-id prefixes written by Pico before local surfaces were unified.
_LEGACY_LOCAL_PREFIXES = ("web:", "cli:")

#: Suffixes SQLite appends to the shadow tables backing an FTS5 virtual table.
#: These are implementation details and must never be written to directly.
_FTS_SHADOW_SUFFIXES = ("_data", "_idx", "_content", "_docsize", "_config")

#: Marker file recording that a workspace has been migrated already.
_MIGRATION_MARKER = ".owner-identity-migrated"

#: Session keys the web workbench wrote while its identity was per browser:
#: ``web:web:<browser id>:<session id>``.  The browser segment is dropped so a
#: session stays reachable after the browser identity changes.
_LEGACY_WEB_SESSION_KEY_RE = re.compile(
    r"^web:web:[A-Za-z0-9_-]{16,96}:(?P<session>[A-Za-z0-9_-]{16,96})$"
)


#: Namespace shared by every web workbench session key.
WEB_SESSION_PREFIX = "web:web:"


def web_session_key(session_id: str) -> str:
    """Return the durable session key for a web workbench session."""
    return f"{WEB_SESSION_PREFIX}{session_id}"


def resolve_owner_id(channel: str, sender_id: str) -> str:
    """Return the durable owner identity for a channel and sender.

    Local surfaces collapse to :data:`LOCAL_OWNER_ID`.  Every other channel
    keeps its per-sender identity so allowlisted third parties stay isolated.
    """
    if channel in LOCAL_CHANNELS:
        return LOCAL_OWNER_ID
    return f"{channel}:{sender_id or 'anonymous'}"


def is_legacy_local_owner(owner_id: object) -> bool:
    """Report whether an owner id predates the local-surface unification."""
    if not isinstance(owner_id, str) or owner_id == LOCAL_OWNER_ID:
        return False
    return owner_id.startswith(_LEGACY_LOCAL_PREFIXES)


def _identity_tables(connection: sqlite3.Connection) -> list[tuple[str, set[str]]]:
    """Return writable tables carrying ``owner_id`` or ``session_key``.

    FTS5 shadow tables are excluded: the virtual table itself accepts a normal
    ``UPDATE``, but writing to its backing storage would corrupt the index.
    """
    rows = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    virtual = {
        name
        for name, sql in rows
        if isinstance(sql, str) and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")
    }
    shadow = {f"{base}{suffix}" for base in virtual for suffix in _FTS_SHADOW_SUFFIXES}
    tables: list[tuple[str, set[str]]] = []
    for name, _sql in rows:
        if name.startswith("sqlite_") or name in shadow:
            continue
        try:
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')}
        except sqlite3.DatabaseError:
            continue
        relevant = columns & {"owner_id", "session_key"}
        if relevant:
            tables.append((name, relevant))
    return tables


def _rewrite_owner_ids(connection: sqlite3.Connection, table: str) -> int:
    """Point legacy local owner rows at :data:`LOCAL_OWNER_ID`."""
    predicate = " OR ".join("owner_id LIKE ?" for _ in _LEGACY_LOCAL_PREFIXES)
    parameters = [
        LOCAL_OWNER_ID,
        *(f"{prefix}%" for prefix in _LEGACY_LOCAL_PREFIXES),
        LOCAL_OWNER_ID,
    ]
    try:
        cursor = connection.execute(
            f'UPDATE "{table}" SET owner_id = ? WHERE ({predicate}) AND owner_id != ?',
            parameters,
        )
    except sqlite3.DatabaseError:
        # Includes IntegrityError when two historical browser identities would
        # collide on a uniqueness constraint.  Leave those rows for manual
        # review rather than silently discarding either side.
        return 0
    return cursor.rowcount if cursor.rowcount > 0 else 0


def _rewrite_session_keys(connection: sqlite3.Connection, table: str) -> int:
    """Drop the browser segment from legacy web session keys."""
    try:
        existing = connection.execute(
            f'SELECT DISTINCT session_key FROM "{table}" WHERE session_key LIKE ?',
            ("web:web:%",),
        ).fetchall()
    except sqlite3.DatabaseError:
        return 0
    rewritten = 0
    for (value,) in existing:
        if not isinstance(value, str):
            continue
        match = _LEGACY_WEB_SESSION_KEY_RE.match(value)
        if match is None:
            continue
        try:
            cursor = connection.execute(
                f'UPDATE "{table}" SET session_key = ? WHERE session_key = ?',
                (web_session_key(match.group("session")), value),
            )
        except sqlite3.DatabaseError:
            continue
        if cursor.rowcount > 0:
            rewritten += cursor.rowcount
    return rewritten


def migrate_workspace_identity(workspace_path: Path, *, force: bool = False) -> dict[str, int]:
    """Unify legacy per-browser identities across a workspace.

    Rewrites legacy local ``owner_id`` values to :data:`LOCAL_OWNER_ID` and
    strips the browser segment from web ``session_key`` values.  Returns a
    mapping of ``"<database>:<table>:<column>"`` to the number of rows changed.

    The operation is idempotent and guarded by a marker file, so it is safe to
    call on every startup.  Remote-channel rows are never touched.
    """
    workspace = Path(workspace_path)
    marker = workspace / _MIGRATION_MARKER
    if marker.exists() and not force:
        return {}

    changed: dict[str, int] = {}
    for database in sorted(workspace.rglob("*.db")):
        try:
            connection = sqlite3.connect(database)
        except sqlite3.Error:
            continue
        try:
            with connection:
                for table, columns in _identity_tables(connection):
                    if "owner_id" in columns:
                        count = _rewrite_owner_ids(connection, table)
                        if count:
                            changed[f"{database.name}:{table}:owner_id"] = count
                    if "session_key" in columns:
                        count = _rewrite_session_keys(connection, table)
                        if count:
                            changed[f"{database.name}:{table}:session_key"] = count
        except sqlite3.DatabaseError:
            continue
        finally:
            connection.close()

    try:
        workspace.mkdir(parents=True, exist_ok=True)
        marker.write_text("Local web and CLI identities unified.\n", encoding="utf-8")
    except OSError:
        # A read-only workspace still gets a correct in-memory result; the
        # migration simply runs again next time.
        pass
    return changed

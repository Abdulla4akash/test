"""SQLite storage for one workspace.

Two kinds of state live in the database, and code keeps them apart:

* Collected state (postings, snapshots, checks, runs, the fetch log and
  automated history) is written only by collection.
* User-owned state (applications, tasks, triage decisions, local boards,
  board settings and user history) is written only by explicit user commands.

`collected_state_only` enforces the split during collection with an SQLite
authorizer, so a bug in collection code cannot touch an application.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 2

USER_TABLES = {"applications", "tasks", "triage", "local_boards", "board_settings", "local_windows"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- user-owned ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS local_boards (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    ats TEXT NOT NULL,
    token TEXT NOT NULL,
    region TEXT,
    kind TEXT,
    careers TEXT,
    added_at TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS board_settings (
    board_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    note TEXT,
    at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS triage (
    posting_id TEXT PRIMARY KEY,
    decision TEXT NOT NULL CHECK (decision IN ('promoted', 'dismissed', 'later')),
    note TEXT,
    at TEXT NOT NULL,
    application_id TEXT
);
CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY,
    posting_id TEXT,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    status TEXT NOT NULL,
    next_action TEXT,
    next_due TEXT,
    notes TEXT,
    applied_at TEXT,
    deadline TEXT,
    deadline_basis TEXT,
    window_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS local_windows (
    id TEXT PRIMARY KEY,
    company TEXT NOT NULL,
    programme TEXT NOT NULL,
    kind TEXT NOT NULL,
    cycle TEXT,
    page TEXT NOT NULL,
    opens_date TEXT,
    closes_date TEXT,
    closes_time TEXT,
    timezone TEXT,
    source TEXT,
    note TEXT,
    reported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    application_id TEXT NOT NULL REFERENCES applications(id),
    text TEXT NOT NULL,
    kind TEXT NOT NULL,
    due TEXT,
    done_at TEXT,
    created_at TEXT NOT NULL
);

-- collected ----------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    budget INTEGER,
    requests INTEGER NOT NULL DEFAULT 0,
    boards TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    run_id INTEGER REFERENCES runs(id),
    board_id TEXT NOT NULL,
    at TEXT NOT NULL,
    outcome TEXT NOT NULL,
    completeness TEXT NOT NULL,
    detail TEXT,
    seen INTEGER NOT NULL DEFAULT 0,
    new INTEGER NOT NULL DEFAULT 0,
    changed INTEGER NOT NULL DEFAULT 0,
    missing INTEGER NOT NULL DEFAULT 0,
    reappeared INTEGER NOT NULL DEFAULT 0,
    quarantined INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY,
    run_id INTEGER,
    board_id TEXT,
    at TEXT NOT NULL,
    url TEXT NOT NULL,
    purpose TEXT NOT NULL,
    status INTEGER,
    result TEXT NOT NULL,
    bytes INTEGER,
    elapsed_ms INTEGER,
    attempt INTEGER NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS postings (
    id TEXT PRIMARY KEY,
    short_id TEXT NOT NULL UNIQUE,
    board_id TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT,
    locations TEXT NOT NULL,
    remote INTEGER,
    department TEXT,
    employment_type TEXT,
    published_at TEXT,
    source_updated_at TEXT,
    content_hash TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('listed', 'missing')),
    missing_since TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    synthetic INTEGER NOT NULL DEFAULT 0,
    UNIQUE (board_id, source_record_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY,
    posting_id TEXT NOT NULL REFERENCES postings(id),
    check_id INTEGER REFERENCES checks(id),
    at TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY,
    check_id INTEGER REFERENCES checks(id),
    board_id TEXT NOT NULL,
    at TEXT NOT NULL,
    reason TEXT NOT NULL,
    raw_id TEXT,
    title TEXT
);

-- both: every row says who acted ----------------------------------------------------
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY,
    at TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK (actor_type IN ('automated', 'user')),
    posting_id TEXT,
    application_id TEXT,
    field TEXT NOT NULL,
    old TEXT,
    new TEXT,
    check_id INTEGER,
    note TEXT
);
CREATE INDEX IF NOT EXISTS postings_board ON postings(board_id, state);
CREATE INDEX IF NOT EXISTS history_posting ON history(posting_id);
CREATE INDEX IF NOT EXISTS history_application ON history(application_id);
"""


class SchemaError(RuntimeError):
    pass


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def create(conn: sqlite3.Connection, *, kind: str, created_at: str) -> None:
    with transaction(conn):
        for statement in _statements(SCHEMA):
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('kind', ?)", (kind,))
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('created_at', ?)", (created_at,))


def _statements(script: str) -> list[str]:
    lines = [line for line in script.splitlines() if not line.strip().startswith("--")]
    return [part.strip() for part in "\n".join(lines).split(";") if part.strip()]


# Each migration takes a workspace from version N to N + 1.
MIGRATIONS = {
    1: [
        "ALTER TABLE applications ADD COLUMN deadline TEXT",
        "ALTER TABLE applications ADD COLUMN deadline_basis TEXT",
        "ALTER TABLE applications ADD COLUMN window_id TEXT",
        # local_windows is created by the schema script below.
    ],
}


def check_schema(conn: sqlite3.Connection) -> None:
    """Open a workspace, migrating an older schema forward in one transaction."""
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    try:
        version = int(row[0]) if row else None
    except (TypeError, ValueError):
        raise SchemaError("workspace has an invalid schema version") from None
    if version is not None and version < SCHEMA_VERSION and version not in MIGRATIONS:
        raise SchemaError(f"no migration from workspace schema version {version}")
    if version is not None and version < SCHEMA_VERSION:
        with transaction(conn):
            while version < SCHEMA_VERSION:
                for statement in MIGRATIONS[version]:
                    conn.execute(statement)
                version += 1
            for statement in _statements(SCHEMA):
                conn.execute(statement)  # CREATE ... IF NOT EXISTS: adds new tables only
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
            )
    if version != SCHEMA_VERSION:
        raise SchemaError(
            f"workspace schema version {version}, this tool expects {SCHEMA_VERSION}; "
            "update eu-job-radar to open it"
        )


def meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


@contextmanager
def transaction(conn: sqlite3.Connection):
    if conn.in_transaction:
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


class UserStateWrite(sqlite3.DatabaseError):
    pass


_COLLECTION_GUARDS = {}


@contextmanager
def collected_state_only(conn: sqlite3.Connection):
    """Refuse any write to a user-owned table while collection runs."""
    previous = _COLLECTION_GUARDS.get(conn)
    denied: list[str] = previous if previous is not None else []

    def authorizer(action, arg1, arg2, dbname, source):
        denied.clear()
        writes = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
        table = arg2 if action == sqlite3.SQLITE_ALTER_TABLE else arg1
        changes_table = action in {
            sqlite3.SQLITE_ALTER_TABLE,
            sqlite3.SQLITE_DROP_TABLE,
            sqlite3.SQLITE_CREATE_TABLE,
        }
        if (action in writes or changes_table) and (table or "").casefold() in USER_TABLES:
            denied.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    if previous is None:
        _COLLECTION_GUARDS[conn] = denied
        conn.set_authorizer(authorizer)
    try:
        yield conn
    except sqlite3.DatabaseError as exc:
        if denied:
            table = denied.pop()
            denied.clear()
            raise UserStateWrite(
                f"collection tried to write user-owned table {table}; refused"
            ) from exc
        raise
    finally:
        if previous is None:
            conn.set_authorizer(None)
            del _COLLECTION_GUARDS[conn]

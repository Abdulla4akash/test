"""Workspaces: a directory holding one database, preferences and a cache.

The default workspace is `$XDG_DATA_HOME/eu-job-radar/workspace` (normally
`~/.local/share/eu-job-radar/workspace`), outside any repository, because it
holds the user's applications. `EU_JOB_RADAR_WORKSPACE` or `--workspace`
choose another. Kinds: `real` (the user's own), `validation` (a scratch
workspace for trying live collection) and `demo` (synthetic data only).
"""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import clock as clocks
from . import db, screening

WORKSPACE_ENV = "EU_JOB_RADAR_WORKSPACE"
DB_NAME = "radar.sqlite3"
PREFERENCES = "preferences.toml"
SETTINGS = "radar.toml"
KINDS = {"real", "validation", "demo"}
SETTINGS_TEMPLATE = """\
# eu-job-radar settings for this workspace.

# Optional: an address or URL added to the user-agent so an API operator can
# reach you. Leave empty to identify the tool by its project URL only.
contact = ""
"""


class WorkspaceError(RuntimeError):
    pass


def default_root() -> Path:
    if os.environ.get(WORKSPACE_ENV):
        return Path(os.environ[WORKSPACE_ENV]).expanduser()
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "eu-job-radar" / "workspace"


def init_workspace(root: Path, *, kind: str = "real", clock=None) -> Path:
    if kind not in KINDS:
        raise WorkspaceError(f"unknown workspace kind {kind!r}")
    clock = clock or clocks.from_environment()
    root = root.expanduser()
    path = root / DB_NAME
    if path.exists():
        raise WorkspaceError(f"{root} already holds a workspace")
    root.mkdir(parents=True, exist_ok=True)
    conn = db.connect(path)
    try:
        db.create(conn, kind=kind, created_at=clocks.iso(clock.now()))
    finally:
        conn.close()
    if not (root / PREFERENCES).exists():
        (root / PREFERENCES).write_text(screening.DEFAULT_PREFERENCES, encoding="utf-8")
    if not (root / SETTINGS).exists():
        (root / SETTINGS).write_text(SETTINGS_TEMPLATE, encoding="utf-8")
    return root


@dataclass
class Session:
    root: Path
    conn: object
    clock: object
    kind: str

    @property
    def now_iso(self) -> str:
        return clocks.iso(self.clock.now())

    @property
    def today(self) -> str:
        return self.clock.now().date().isoformat()

    def preferences(self) -> screening.Preferences:
        return screening.load_preferences(self.root / PREFERENCES)

    def settings(self) -> dict:
        path = self.root / SETTINGS
        if not path.exists():
            return {}
        try:
            return tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise WorkspaceError(f"{path}: {exc}") from None

    def close(self) -> None:
        self.conn.close()


def open_session(root: Path | None = None, *, clock=None) -> Session:
    root = (root or default_root()).expanduser()
    path = root / DB_NAME
    if not path.exists():
        raise WorkspaceError(f"no workspace at {root}; run `job-radar init` first")
    conn = db.connect(path)
    db.check_schema(conn)
    return Session(root, conn, clock or clocks.from_environment(), db.meta(conn, "kind") or "real")

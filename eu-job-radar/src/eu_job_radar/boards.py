"""The board registry: the shipped list plus boards the user added.

Shipped boards come from `data/boards.toml`. Boards the user adds live in
the workspace (`local_boards`), and a user can disable or re-enable any
board (`board_settings`); both are user-owned. Health comes from the stored
checks, so it reflects what actually happened on the user's own runs.
"""

import re
import tomllib
from importlib import resources
from urllib.parse import urlsplit

from .adapters import TOKEN_PATTERN, Board, registry

BOARD_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")


class BoardError(ValueError):
    pass


def shipped() -> list[Board]:
    text = resources.files("eu_job_radar").joinpath("data/boards.toml").read_text("utf-8")
    data = tomllib.loads(text)
    boards = []
    for raw in data.get("boards", []):
        boards.append(
            Board(
                id=raw["id"],
                company=raw["company"],
                ats=raw["ats"],
                token=raw["token"],
                kind=raw.get("kind"),
                careers=raw.get("careers"),
                region=raw.get("region"),
                token_status=raw.get("token_status", "unverified"),
            )
        )
    return boards


def validate_board(board: Board) -> list[str]:
    errors = []
    if not BOARD_ID.match(board.id):
        errors.append(f"{board.id}: id must be lower-case letters, digits and hyphens")
    if board.ats not in registry():
        errors.append(f"{board.id}: unsupported ATS {board.ats!r}")
    if not TOKEN_PATTERN.match(board.token or ""):
        errors.append(f"{board.id}: invalid token {board.token!r}")
    if board.region not in (None, "eu"):
        errors.append(f"{board.id}: region must be 'eu' or absent")
    return errors


def all_boards(conn) -> list[Board]:
    boards = {b.id: b for b in shipped()}
    for row in conn.execute("SELECT * FROM local_boards ORDER BY id"):
        boards[row["id"]] = Board(
            id=row["id"],
            company=row["company"],
            ats=row["ats"],
            token=row["token"],
            kind=row["kind"],
            careers=row["careers"],
            region=row["region"],
            token_status="user-added",
            origin="workspace",
            synthetic=bool(row["synthetic"]),
        )
    return sorted(boards.values(), key=lambda b: b.company.casefold())


def enabled_map(conn) -> dict[str, tuple[bool, str | None]]:
    return {
        row["board_id"]: (bool(row["enabled"]), row["note"])
        for row in conn.execute("SELECT * FROM board_settings")
    }


def get(conn, board_id: str) -> Board:
    for board in all_boards(conn):
        if board.id == board_id:
            return board
    raise BoardError(f"no board '{board_id}'; `job-radar boards` lists them")


# --- reading a careers-board URL --------------------------------------------------------

_URL_RULES = [
    # (ats, host pattern, path pattern giving the token, region)
    ("greenhouse", r"(job-)?boards(-api)?\.greenhouse\.io", r"^/(?:v1/boards/)?([^/?#]+)", None),
    ("lever", r"jobs\.lever\.co", r"^/([^/?#]+)", None),
    ("lever", r"jobs\.eu\.lever\.co", r"^/([^/?#]+)", "eu"),
    ("ashby", r"jobs\.ashbyhq\.com", r"^/([^/?#]+)", None),
]


def parse_board_url(url: str) -> tuple[str, str, str | None]:
    """Find (ats, token, region) in a public careers-board URL such as
    https://jobs.lever.co/acme or https://boards.greenhouse.io/acme/jobs/123."""
    parts = urlsplit(url.strip())
    if parts.scheme not in {"https", "http"} or not parts.hostname:
        raise BoardError(f"not a web address: {url}")
    host = parts.hostname.lower()
    for ats, host_pattern, path_pattern, region in _URL_RULES:
        if re.fullmatch(host_pattern, host):
            match = re.match(path_pattern, parts.path)
            if match and TOKEN_PATTERN.match(match.group(1)) and match.group(1) != "embed":
                return ats, match.group(1), region
            raise BoardError(f"{url}: no board name in the path")
    raise BoardError(
        f"{host} is not a supported board host. Supported: boards.greenhouse.io, "
        "job-boards.greenhouse.io, jobs.lever.co, jobs.eu.lever.co, jobs.ashbyhq.com. "
        "Many careers pages embed one of these; open a job and look at its address."
    )


def slug(company: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", company.casefold()).strip("-")
    return text[:60] or "board"

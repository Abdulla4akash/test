"""Adapters for applicant-tracking-system job board APIs.

Each adapter reads one company's whole public board in one request and
returns a BoardResult. Adapters reach the network only through the
`SourceFetcher` they are given (fetch.py), report honestly what they read,
and never invent an id: an item without a stable id or title is quarantined,
not stored.

Outcomes:
* `complete`: the whole board was read and every item was usable. Only a
  complete read can mark postings that disappeared as missing.
* `empty`: the whole board was read and it lists no jobs.
* `partial`: the board was read but some items were unusable, or the API
  said it holds more jobs than it returned. Absent postings are not marked.
* `not_found`: the board token does not exist (HTTP 404).
* `fetch_failed`, `parse_failed`: nothing usable was read.
"""

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from .. import fetch

TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


@dataclass
class Board:
    id: str
    company: str
    ats: str
    token: str
    kind: str | None = None
    careers: str | None = None
    region: str | None = None  # Lever: "eu" for accounts hosted in the EU
    token_status: str = "unverified"
    origin: str = "registry"  # registry | workspace
    synthetic: bool = False
    checked_on: str | None = None


@dataclass
class BoardResult:
    outcome: str
    detail: str | None = None
    postings: list[dict] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)

    @property
    def inventory_complete(self) -> bool:
        ids = [p["source_record_id"] for p in self.postings]
        return (
            self.outcome in {"complete", "empty"}
            and not self.quarantined
            and len(ids) == len(set(ids))
        )


def clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text[:300] or None


def clean_url(value) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.netloc or "@" in parts.netloc:
        return None
    return value.strip()[:1000]


def iso_from_millis(value) -> str | None:
    if not isinstance(value, int | float) or isinstance(value, bool) or value <= 0:
        return None
    try:
        if not math.isfinite(value):
            return None
        return (
            datetime.fromtimestamp(value / 1000, UTC)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (OverflowError, OSError, ValueError):
        return None


def iso_text(value) -> str | None:
    """Keep an ISO date or datetime from the API as given (trimmed), or None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text[:40]


class Adapter:
    ats = ""
    label = ""
    hosts: list[str] = []

    def host(self, board: Board) -> str:
        return self.hosts[0]

    def url(self, board: Board) -> str:  # pragma: no cover - abstract
        raise NotImplementedError

    def parse(self, data, board: Board) -> BoardResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def collect(self, fetcher: fetch.SourceFetcher, board: Board) -> BoardResult:
        if not TOKEN_PATTERN.match(board.token):
            return BoardResult("fetch_failed", f"invalid board token {board.token!r}")
        try:
            response = fetcher.get(self.url(board), expect="json", purpose="board")
        except fetch.NotFound:
            return BoardResult("not_found", f"{self.label} has no board '{board.token}' (HTTP 404)")
        except fetch.FetchError as exc:
            return BoardResult("fetch_failed", f"{exc.kind}: {exc}")
        try:
            data = json.loads(response.text)
        except ValueError as exc:
            return BoardResult("parse_failed", f"response is not JSON: {exc}")
        try:
            result = self.parse(data, board)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            return BoardResult("parse_failed", f"unexpected {self.label} response shape: {exc}")
        seen = set()
        for item in result.postings:
            if item["source_record_id"] in seen:
                quarantine(
                    result, "duplicate id in board response", {"id": item["source_record_id"]}
                )
            seen.add(item["source_record_id"])
        if result.outcome == "complete" and result.quarantined:
            result.outcome = "partial"
            result.detail = (
                f"{len(result.quarantined)} item(s) unusable; missing postings not marked"
            )
        if result.outcome == "complete" and not result.postings:
            result.outcome = "empty"
        if response.from_cache:
            result.detail = ((result.detail + "; ") if result.detail else "") + "not modified"
        return result


def token_path(token: str) -> str:
    return quote(token, safe="")


def quarantine(result: BoardResult, reason: str, item) -> None:
    raw_id = item.get("id") if isinstance(item, dict) else None
    title = item.get("title") or item.get("text") if isinstance(item, dict) else None
    result.quarantined.append(
        {"reason": reason, "raw_id": None if raw_id is None else str(raw_id)[:100],
         "title": clean_text(title)}
    )  # fmt: skip


def posting(
    *,
    record_id,
    title,
    url,
    locations,
    remote=None,
    department=None,
    employment_type=None,
    published_at=None,
    source_updated_at=None,
) -> dict:
    seen, clean_locations = set(), []
    for value in locations:
        text = clean_text(value)
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            clean_locations.append(text)
    return {
        "source_record_id": str(record_id),
        "title": clean_text(title),
        "url": clean_url(url),
        "locations": clean_locations,
        "remote": remote,
        "department": clean_text(department),
        "employment_type": clean_text(employment_type),
        "published_at": published_at,
        "source_updated_at": source_updated_at,
    }


def valid_id(value) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    if isinstance(value, int):
        return value > 0
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", value))


def registry() -> dict[str, Adapter]:
    from .ashby import AshbyAdapter
    from .greenhouse import GreenhouseAdapter
    from .lever import LeverAdapter

    return {a.ats: a for a in (GreenhouseAdapter(), LeverAdapter(), AshbyAdapter())}

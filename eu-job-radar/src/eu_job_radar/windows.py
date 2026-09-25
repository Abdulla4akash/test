"""Hiring windows: internship and graduate programmes with dated claims.

Large employers (Google, Meta, Amazon, Microsoft, the trading firms) have no
public job-board API, and their deadlines matter more than any single
posting. Each window in `data/windows.toml` holds claims about when it opens
and closes, each with a basis (see that file):

* `current_cycle_announcement`: read on the official page, with wording.
* `owner_reported`: reported by the repository owner, shipped labelled.
* `user_reported`: reported by you, stored in your workspace.
* `not_yet_verified`: unknown.

Only an announcement is a fixed deadline. Reported dates are shown with
their label so they are never mistaken for checked ones, and nothing turns a
`pattern` ("usually opens in autumn") into a date.
"""

import re
import tomllib
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from importlib import resources
from urllib.parse import urlsplit

from . import db

BASES = {
    "current_cycle_announcement": "announced on the official page",
    "owner_reported": "reported by the repository owner, not checked on the official page",
    "user_reported": "reported by you, not checked on the official page",
    "not_yet_verified": "not yet known",
}
DATED_BASES = {"current_cycle_announcement", "owner_reported", "user_reported"}
KINDS = {"internship", "graduate", "placement", "other"}
WINDOW_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class WindowError(ValueError):
    pass


@dataclass
class Claim:
    basis: str = "not_yet_verified"
    date: str | None = None
    time: str | None = None
    timezone: str | None = None
    wording: str | None = None
    source: str | None = None
    note: str | None = None
    reported_on: str | None = None
    checked_on: str | None = None

    @property
    def dated(self) -> bool:
        return self.basis in DATED_BASES and self.date is not None

    @property
    def fixed(self) -> bool:
        return self.basis == "current_cycle_announcement" and self.date is not None

    def describe(self) -> str:
        if not self.dated:
            return "unknown"
        when = (
            self.date
            + (f" {self.time}" if self.time else "")
            + (f" {self.timezone}" if self.timezone else "")
        )
        missing = [name for name in ("time", "timezone") if not getattr(self, name)]
        if missing:
            when += " (" + " and ".join(missing) + " not stated)"
        return f"{when}, {BASES[self.basis]}"


@dataclass
class Window:
    id: str
    company: str
    programme: str
    kind: str
    cycle: str
    page: str
    where: str | None = None
    pattern: str | None = None
    opens: Claim = field(default_factory=Claim)
    closes: Claim = field(default_factory=Claim)
    origin: str = "registry"  # registry | workspace

    @property
    def host(self) -> str:
        return (urlsplit(self.page).hostname or "").lower()


def _claim(raw: dict | None, where: str) -> Claim:
    raw = raw or {}
    claim = Claim(**{k: raw.get(k) for k in Claim.__dataclass_fields__ if k in raw})
    if claim.basis not in BASES:
        raise WindowError(f"{where}: unknown basis {claim.basis!r}")
    if claim.date is not None:
        _date(claim.date, where)
        if claim.basis == "not_yet_verified":
            raise WindowError(f"{where}: an unverified claim cannot have a date")
    if claim.time is not None:
        _time(claim.time)
    if claim.basis in DATED_BASES and claim.date is None:
        raise WindowError(f"{where}: basis {claim.basis} needs a date")
    if claim.basis == "current_cycle_announcement" and not claim.wording:
        raise WindowError(f"{where}: an announcement needs the quoted wording")
    if claim.checked_on is not None:
        _date(claim.checked_on, where + " checked_on")
    if claim.basis == "current_cycle_announcement":
        if not claim.source or not claim.checked_on:
            raise WindowError(f"{where}: an announcement needs source and checked_on")
        if urlsplit(claim.source).scheme != "https" or not urlsplit(claim.source).hostname:
            raise WindowError(f"{where}: announcement source must be an https address")
    return claim


def _date(value: str, where: str) -> str:
    try:
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise WindowError(f"{where}: date must be YYYY-MM-DD, got {value!r}") from None


def _time(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise WindowError("--time must be HH:MM, from 00:00 to 23:59")


def shipped(text: str | None = None) -> list[Window]:
    if text is None:
        text = resources.files("eu_job_radar").joinpath("data/windows.toml").read_text("utf-8")
    data = tomllib.loads(text)
    out, seen = [], set()
    for raw in data.get("windows", []):
        where = f"windows.toml [{raw.get('id')}]"
        window = Window(
            id=raw["id"],
            company=raw["company"],
            programme=raw["programme"],
            kind=raw.get("kind", "other"),
            cycle=str(raw.get("cycle", "")),
            page=raw["page"],
            where=raw.get("where"),
            pattern=raw.get("pattern"),
            opens=_claim(raw.get("opens"), where + " opens"),
            closes=_claim(raw.get("closes"), where + " closes"),
        )
        _validate(window, where)
        if window.id in seen:
            raise WindowError(f"{where}: duplicate id")
        seen.add(window.id)
        out.append(window)
    return out


def _validate(window: Window, where: str) -> None:
    if not WINDOW_ID.match(window.id):
        raise WindowError(f"{where}: id must be lower-case letters, digits and hyphens")
    if window.kind not in KINDS:
        raise WindowError(f"{where}: kind must be one of {sorted(KINDS)}")
    parts = urlsplit(window.page)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise WindowError(f"{where}: page must be an https address")


def all_windows(conn) -> list[Window]:
    """Shipped windows with your reports applied, plus windows you added."""
    windows = {w.id: w for w in shipped()}
    for row in conn.execute("SELECT * FROM local_windows ORDER BY id"):
        claim = Claim(
            basis="user_reported",
            date=row["closes_date"],
            time=row["closes_time"],
            timezone=row["timezone"],
            source=row["source"],
            note=row["note"],
            reported_on=row["reported_at"][:10],
        )
        opens = (
            Claim(
                basis="user_reported", date=row["opens_date"], reported_on=row["reported_at"][:10]
            )
            if row["opens_date"]
            else None
        )
        base = windows.get(row["id"])
        if base is not None:
            if row["cycle"] != base.cycle:
                continue  # reports from an older cycle cannot supply this cycle's dates
            # A report on a shipped window. An announcement is never overridden.
            if row["closes_date"] and not base.closes.fixed:
                base.closes = claim
            if opens and not base.opens.fixed:
                base.opens = opens
            continue
        windows[row["id"]] = Window(
            id=row["id"],
            company=row["company"],
            programme=row["programme"],
            kind=row["kind"],
            cycle=row["cycle"] or "",
            page=row["page"],
            opens=opens or Claim(),
            closes=claim if row["closes_date"] else Claim(),
            origin="workspace",
        )
    return sorted(windows.values(), key=lambda w: (w.company.casefold(), w.id))


def get(conn, window_id: str) -> Window:
    for window in all_windows(conn):
        if window.id == window_id:
            return window
    raise WindowError(f"no window '{window_id}'; `job-radar windows` lists them")


def report(session, window_id: str, *, closes=None, opens=None, time=None, timezone=None,
           source=None, note=None, company=None, programme=None, page=None, kind="internship",
           cycle=None) -> str:  # fmt: skip
    """Record a date you found. For a shipped window only the dates are
    needed; a new window also needs company, programme and page."""
    if not closes and not opens:
        raise WindowError("give --closes and/or --opens")
    closes = _date(closes, "--closes") if closes else None
    opens = _date(opens, "--opens") if opens else None
    if time:
        _time(time)
    if source and (
        urlsplit(source).scheme not in {"http", "https"} or not urlsplit(source).hostname
    ):
        raise WindowError("--source must be a web address")
    shipped_ids = {w.id for w in shipped()}
    conn = session.conn
    previous = conn.execute("SELECT * FROM local_windows WHERE id = ?", (window_id,)).fetchone()
    if window_id not in shipped_ids:
        if previous:
            company = company or previous["company"]
            programme = programme or previous["programme"]
            page = page or previous["page"]
            kind = previous["kind"]
            cycle = cycle or previous["cycle"]
        if not (company and programme and page):
            raise WindowError(
                f"'{window_id}' is not a shipped window; a new one needs --company, --programme"
                " and --page"
            )
        new = Window(window_id, company, programme, kind, cycle or "", page)
        _validate(new, window_id)
    else:
        base = get(conn, window_id)
        if closes and base.closes.fixed:
            raise WindowError(
                f"{window_id} already has an announced closing date ({base.closes.describe()})"
            )
        if opens and base.opens.fixed:
            raise WindowError(f"{window_id} already has an announced opening date")
        company, programme, page = base.company, base.programme, base.page
        kind, cycle = base.kind, base.cycle
    if previous and previous["cycle"] == cycle:
        # Reporting one endpoint must not erase the other endpoint or its evidence.
        opens = opens if opens is not None else previous["opens_date"]
        closes = closes if closes is not None else previous["closes_date"]
        time = time if time is not None else previous["closes_time"]
        timezone = timezone if timezone is not None else previous["timezone"]
        source = source if source is not None else previous["source"]
        note = note if note is not None else previous["note"]
    if opens and closes and opens > closes:
        raise WindowError("opening date must not be after closing date")
    if (time or timezone) and not closes:
        raise WindowError("--time and --timezone describe --closes; give a closing date")
    with db.transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO local_windows (id, company, programme, kind, cycle, page,"
            " opens_date, closes_date, closes_time, timezone, source, note, reported_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (window_id, company, programme, kind, cycle, page, opens, closes, time, timezone,
             source, note, session.now_iso),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO history (at, actor_type, field, new, note) VALUES (?, 'user', ?, ?, ?)",
            (session.now_iso, f"window:{window_id}", closes or opens, note),
        )
    return window_id


def deadlines(session, *, days: int = 60, include_unknown: bool = False) -> list[dict]:
    """Dated closings and openings from windows, and application deadlines,
    soonest first, from today up to `days` ahead (and anything overdue that
    an active application still carries)."""
    from .applications import ACTIVE

    if days < 0:
        raise WindowError("--days must be zero or greater")
    today = date.fromisoformat(session.today)
    out = []
    tracked = {
        (row[0], row[1], row[2])
        for row in session.conn.execute(
            "SELECT window_id, deadline, deadline_basis FROM applications"
            " WHERE window_id IS NOT NULL AND deadline IS NOT NULL"
            f" AND status IN ({','.join('?' * len(ACTIVE))})",
            ACTIVE,
        )
    }
    for w in all_windows(session.conn):
        for label, claim in (("closes", w.closes), ("opens", w.opens)):
            if not claim.dated or (
                label == "closes" and (w.id, claim.date, claim.basis) in tracked
            ):
                continue  # a tracked window shows once, as your application
            left = (date.fromisoformat(claim.date) - today).days
            if 0 <= left <= days or (label == "closes" and -3 <= left < 0):
                out.append({"what": f"{w.company}: {w.programme}", "event": label,
                            "date": claim.date, "days_left": left, "basis": claim.basis,
                            "detail": claim.describe(), "ref": w.id, "page": claim.source or w.page})  # fmt: skip
        if include_unknown and not w.closes.dated:
            out.append({"what": f"{w.company}: {w.programme}", "event": "closes", "date": None,
                        "days_left": None, "basis": "not_yet_verified", "detail": w.pattern or "",
                        "ref": w.id, "page": w.page})  # fmt: skip
    for app in session.conn.execute(
        "SELECT * FROM applications WHERE deadline IS NOT NULL"
        f" AND status IN ({','.join('?' * len(ACTIVE))})",
        ACTIVE,
    ):
        left = (date.fromisoformat(app["deadline"]) - today).days
        if left <= days:
            out.append({"what": f"{app['company']}: {app['title']}", "event": "application deadline",
                        "date": app["deadline"], "days_left": left,
                        "basis": app["deadline_basis"] or "user_reported",
                        "detail": BASES.get(app["deadline_basis"] or "user_reported", ""),
                        "ref": app["id"], "page": app["url"]})  # fmt: skip
    out.sort(key=lambda d: (d["date"] is None, d["date"] or "", d["what"]))
    return out


# --- re-reading official pages --------------------------------------------------------


@dataclass
class PageCheck:
    window: Window
    status: str  # present | absent | reachable | failed | not_checked
    detail: str = ""


def _fold(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", unescape(text)).casefold()


def check_pages(session, ids: list[str] | None = None, *, budget: int = 30) -> list[PageCheck]:
    """Fetch each window's official page once and report whether its quoted
    wording is still there. Never edits the registry or your reports."""
    from . import collect, fetch

    if budget < 1:
        raise WindowError("the request budget must be at least 1")
    if session.kind == "demo":
        raise WindowError("demo workspaces cannot check live programme pages")
    chosen = [w for w in all_windows(session.conn) if not ids or w.id in ids]
    if ids:
        unknown = set(ids) - {w.id for w in chosen}
        if unknown:
            raise WindowError(f"no window {sorted(unknown)[0]!r}")
    client = fetch.HttpClient(
        policy=fetch.FetchPolicy(user_agent=fetch.user_agent(collect._contact(session))),
        transport=collect.TRANSPORT_FACTORY() if collect.TRANSPORT_FACTORY else None,
        resolver=collect.RESOLVER,
        timer=collect.TIMER,
        clock=session.clock,
        cache=fetch.ResponseCache(session.root / "cache" / "http"),
        run_budget=fetch.Budget(budget, "run"),
    )
    results, texts = [], {}
    for w in chosen:
        claims = [c for c in (w.opens, w.closes) if c.wording]
        targets = list(dict.fromkeys(c.source or w.page for c in claims)) or [w.page]
        for target in targets:
            fetcher = client.for_source(
                f"window:{w.id}", hosts=[urlsplit(target).hostname], cache_retention_days=2.0
            )
            if target not in texts:
                try:
                    texts[target] = _fold(fetcher.get(target, expect="html", purpose="page").text)
                except fetch.FetchError as exc:
                    texts[target] = exc
        failures = [texts[t] for t in targets if isinstance(texts[t], Exception)]
        if failures:
            results.append(PageCheck(w, "failed", "; ".join(f"{e.kind}: {e}" for e in failures)))
        elif not claims:
            results.append(PageCheck(w, "reachable", "no quoted wording to compare yet"))
        else:
            missing = [
                c.wording for c in claims if _fold(c.wording) not in texts[c.source or w.page]
            ]
            results.append(
                PageCheck(w, "absent" if missing else "present",
                          "missing: " + " | ".join(missing) if missing else "all quotes found")
            )  # fmt: skip
    collect._log_requests(session.conn, None, client.log)
    return results

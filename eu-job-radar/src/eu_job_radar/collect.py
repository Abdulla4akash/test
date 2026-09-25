"""Collection runs: read chosen boards once, store what was read, account for it.

A run happens only when the user runs `job-radar collect`; nothing is
scheduled. One run per workspace at a time (an OS file lock). Each board is
one request to its ATS API through the shared HTTP layer, which enforces the
network-safety rules, robots.txt, one request per second per API host, the
run's request budget and conditional requests. One board's failure never
stops the others, and every request is logged.
"""

import fcntl
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field

from . import boards as boards_mod
from . import db, fetch, ingest
from .adapters import Board, BoardResult, registry

# Test and demo hooks: a scripted transport, resolver and timer.
TRANSPORT_FACTORY = None
RESOLVER = None
TIMER = None

DEFAULT_BUDGET = 80
CACHE_RETENTION_DAYS = 2.0


class CollectError(RuntimeError):
    pass


@dataclass
class BoardOutcome:
    board: Board
    outcome: str
    detail: str | None
    counts: dict = field(default_factory=dict)
    requests: int = 0


@dataclass
class RunSummary:
    run_id: int
    status: str
    requests: int
    budget: int
    results: list[BoardOutcome]
    refused: list[tuple[str, str]]


@contextmanager
def run_lock(root):
    path = root / "collect.lock"
    handle = open(path, "a+")  # noqa: SIM115 - held for the whole run
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectError("another collection run is in progress in this workspace") from None
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        yield
    finally:
        handle.close()


def select_boards(conn, ids: list[str], *, all_enabled: bool) -> tuple[list[Board], list]:
    known = {b.id: b for b in boards_mod.all_boards(conn)}
    settings = boards_mod.enabled_map(conn)
    refused, chosen = [], []
    if all_enabled:
        ids = list(known)
    if not ids:
        raise CollectError("name one or more boards, or pass --all")
    for board_id in dict.fromkeys(ids):
        board = known.get(board_id)
        if board is None:
            refused.append((board_id, "no such board"))
            continue
        enabled, note = settings.get(board_id, (True, None))
        if not enabled:
            if not all_enabled:
                refused.append((board_id, "disabled" + (f" ({note})" if note else "")))
            continue
        errors = boards_mod.validate_board(board)
        if errors:
            refused.append((board_id, "; ".join(errors)))
            continue
        chosen.append(board)
    return chosen, refused


def run(session, ids: list[str], *, all_enabled: bool = False, budget: int = DEFAULT_BUDGET):
    if budget < 1:
        raise CollectError("the request budget must be at least 1")
    conn = session.conn
    chosen, refused = select_boards(conn, ids, all_enabled=all_enabled)
    if not chosen:
        reasons = "; ".join(f"{b}: {why}" for b, why in refused) or "no enabled boards"
        raise CollectError(f"nothing to collect ({reasons})")
    if session.kind == "demo" and any(not b.synthetic for b in chosen):
        raise CollectError("a demo workspace collects only its synthetic boards")
    with run_lock(session.root):
        with db.collected_state_only(conn), db.transaction(conn):
            conn.execute(
                "UPDATE runs SET status = 'interrupted', finished_at = ? WHERE status = 'running'",
                (session.now_iso,),
            )
            run_id = conn.execute(
                "INSERT INTO runs (started_at, status, budget, boards) VALUES (?, 'running', ?, ?)",
                (session.now_iso, budget, json.dumps([b.id for b in chosen])),
            ).lastrowid
        client = fetch.HttpClient(
            policy=fetch.FetchPolicy(user_agent=fetch.user_agent(_contact(session))),
            transport=TRANSPORT_FACTORY() if TRANSPORT_FACTORY else None,
            resolver=RESOLVER,
            timer=TIMER,
            clock=session.clock,
            cache=fetch.ResponseCache(session.root / "cache" / "http"),
            run_budget=fetch.Budget(budget, "run"),
        )
        client.cache.prune(session.clock.now(), CACHE_RETENTION_DAYS)
        adapters = registry()
        results = []
        for board in chosen:
            adapter = adapters[board.ats]
            fetcher = client.for_source(
                board.id,
                hosts=adapter.hosts,
                rate_group=f"ats:{adapter.ats}",
                cache_retention_days=CACHE_RETENTION_DAYS,
            )
            if client.run_budget.remaining == 0:
                result = BoardResult("fetch_failed", "run request budget reached; not attempted")
            else:
                result = adapter.collect(fetcher, board)
            counts = ingest.store_check(
                conn,
                board=board,
                result=result,
                run_id=run_id,
                now=session.now_iso,
                requests=fetcher.requests,
            )
            _log_requests(conn, run_id, fetcher.records)
            results.append(
                BoardOutcome(board, result.outcome, result.detail, counts, fetcher.requests)
            )
        requests = client.run_budget.used
        with db.collected_state_only(conn), db.transaction(conn):
            conn.execute(
                "UPDATE runs SET status = 'finished', finished_at = ?, requests = ? WHERE id = ?",
                (session.now_iso, requests, run_id),
            )
    return RunSummary(run_id, "finished", requests, budget, results, refused)


def _contact(session) -> str | None:
    value = session.settings().get("contact")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _log_requests(conn, run_id, records) -> None:
    with db.collected_state_only(conn), db.transaction(conn):
        for r in records:
            conn.execute(
                "INSERT INTO fetch_log (run_id, board_id, at, url, purpose, status, result, bytes,"
                " elapsed_ms, attempt, detail) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    r.source_id,
                    r.at,
                    r.url,
                    r.purpose,
                    r.status,
                    r.result,
                    r.bytes,
                    r.elapsed_ms,
                    r.attempt,
                    r.detail,
                ),
            )


def health(conn) -> dict[str, dict]:
    """Per board: last check, last outcome, consecutive failures, listing counts."""
    out: dict[str, dict] = {}
    for row in conn.execute("SELECT * FROM checks ORDER BY id"):
        info = out.setdefault(row["board_id"], {"checks": 0, "failures_in_a_row": 0})
        info["checks"] += 1
        info["last_at"] = row["at"]
        info["last_outcome"] = row["outcome"]
        info["last_detail"] = row["detail"]
        if row["outcome"] in {"complete", "empty", "partial"}:
            info["last_success"] = row["at"]
            info["failures_in_a_row"] = 0
        else:
            info["failures_in_a_row"] += 1
    for row in conn.execute(
        "SELECT board_id, state, COUNT(*) AS n FROM postings GROUP BY board_id, state"
    ):
        out.setdefault(row["board_id"], {"checks": 0, "failures_in_a_row": 0})[row["state"]] = row[
            "n"
        ]
    return out

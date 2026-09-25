"""Storing one board check.

A posting is one board's job (`board_id`, `source_record_id`). Its content is
the fields the API gave; a snapshot is stored whenever that content changes,
and every automated change writes a history row naming the field and check.

Absence is handled carefully:

* Only a complete read of a board (`complete` or `empty`) can mark postings
  that were not seen as `missing`. A partial read, a failure or a 404 never
  does, so a network problem cannot make jobs look withdrawn.
* `missing` is shown, never deleted, and an application linked to a missing
  posting keeps its status. A posting seen again becomes `listed` again, with
  a history row.
"""

import hashlib
import json

from . import db
from .adapters import Board, BoardResult

CONTENT_FIELDS = (
    "title",
    "url",
    "locations",
    "remote",
    "department",
    "employment_type",
    "published_at",
)


def content_of(posting: dict) -> dict:
    return {field: posting.get(field) for field in CONTENT_FIELDS}


def content_hash(content: dict) -> str:
    text = json.dumps(content, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def posting_id(board_id: str, record_id: str) -> str:
    return f"{board_id}:{record_id}"


def short_id(pid: str) -> str:
    return "p-" + hashlib.sha1(pid.encode("utf-8")).hexdigest()[:7]


def _history(conn, at, pid, field, old, new, check_id, note=None):
    conn.execute(
        "INSERT INTO history (at, actor_type, posting_id, field, old, new, check_id, note)"
        " VALUES (?, 'automated', ?, ?, ?, ?, ?, ?)",
        (at, pid, field, _text(old), _text(new), check_id, note),
    )


def _text(value):
    if value is None or isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def store_check(
    conn, *, board: Board, result: BoardResult, run_id: int | None, now: str, requests: int = 0
) -> dict:
    """Store one board check in one transaction. Returns the check's counts."""
    counts = {"seen": 0, "new": 0, "changed": 0, "missing": 0, "reappeared": 0}
    counts["quarantined"] = len(result.quarantined)
    with db.collected_state_only(conn), db.transaction(conn):
        cursor = conn.execute(
            "INSERT INTO checks (run_id, board_id, at, outcome, completeness, detail, requests)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                board.id,
                now,
                result.outcome,
                "complete"
                if result.inventory_complete
                else "partial"
                if result.postings
                else "none",
                result.detail,
                requests,
            ),
        )
        check_id = cursor.lastrowid
        seen_ids = set()
        for posting in result.postings:
            pid = posting_id(board.id, posting["source_record_id"])
            if pid in seen_ids:
                continue  # the same id twice in one response: keep the first
            seen_ids.add(pid)
            counts["seen"] += 1
            content = content_of(posting)
            digest = content_hash(content)
            row = conn.execute("SELECT * FROM postings WHERE id = ?", (pid,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO postings (id, short_id, board_id, source_record_id, company, title,"
                    " url, locations, remote, department, employment_type, published_at,"
                    " source_updated_at, content_hash, state, first_seen_at, last_seen_at,"
                    " synthetic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'listed', ?, ?,"
                    " ?)",
                    (
                        pid,
                        short_id(pid),
                        board.id,
                        posting["source_record_id"],
                        board.company,
                        content["title"],
                        content["url"],
                        json.dumps(content["locations"], ensure_ascii=False),
                        None if content["remote"] is None else int(content["remote"]),
                        content["department"],
                        content["employment_type"],
                        content["published_at"],
                        posting.get("source_updated_at"),
                        digest,
                        now,
                        now,
                        int(board.synthetic),
                    ),
                )
                _snapshot(conn, pid, check_id, now, content, digest)
                _history(conn, now, pid, "posting", None, "listed", check_id, "first seen")
                counts["new"] += 1
                continue
            if row["content_hash"] != digest:
                old = _row_content(row)
                for field in CONTENT_FIELDS:
                    if old[field] != content[field]:
                        _history(conn, now, pid, field, old[field], content[field], check_id)
                conn.execute(
                    "UPDATE postings SET title = ?, url = ?, locations = ?, remote = ?,"
                    " department = ?, employment_type = ?, published_at = ?, content_hash = ?"
                    " WHERE id = ?",
                    (
                        content["title"],
                        content["url"],
                        json.dumps(content["locations"], ensure_ascii=False),
                        None if content["remote"] is None else int(content["remote"]),
                        content["department"],
                        content["employment_type"],
                        content["published_at"],
                        digest,
                        pid,
                    ),
                )
                _snapshot(conn, pid, check_id, now, content, digest)
                counts["changed"] += 1
            if row["state"] == "missing":
                _history(conn, now, pid, "state", "missing", "listed", check_id, "listed again")
                counts["reappeared"] += 1
            conn.execute(
                "UPDATE postings SET state = 'listed', missing_since = NULL, last_seen_at = ?,"
                " source_updated_at = ? WHERE id = ?",
                (now, posting.get("source_updated_at"), pid),
            )
        if result.inventory_complete:
            for row in conn.execute(
                "SELECT id FROM postings WHERE board_id = ? AND state = 'listed'", (board.id,)
            ).fetchall():
                if row["id"] not in seen_ids:
                    conn.execute(
                        "UPDATE postings SET state = 'missing', missing_since = ? WHERE id = ?",
                        (now, row["id"]),
                    )
                    _history(
                        conn,
                        now,
                        row["id"],
                        "state",
                        "listed",
                        "missing",
                        check_id,
                        "absent from a complete read of the board",
                    )
                    counts["missing"] += 1
        for item in result.quarantined:
            conn.execute(
                "INSERT INTO quarantine (check_id, board_id, at, reason, raw_id, title)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (check_id, board.id, now, item["reason"], item.get("raw_id"), item.get("title")),
            )
        conn.execute(
            "UPDATE checks SET seen = ?, new = ?, changed = ?, missing = ?, reappeared = ?,"
            " quarantined = ? WHERE id = ?",
            (
                counts["seen"],
                counts["new"],
                counts["changed"],
                counts["missing"],
                counts["reappeared"],
                counts["quarantined"],
                check_id,
            ),
        )
    counts["check_id"] = check_id
    return counts


def _snapshot(conn, pid, check_id, now, content, digest):
    conn.execute(
        "INSERT INTO snapshots (posting_id, check_id, at, content, content_hash)"
        " VALUES (?, ?, ?, ?, ?)",
        (pid, check_id, now, json.dumps(content, ensure_ascii=False, sort_keys=True), digest),
    )


def _row_content(row) -> dict:
    return {
        "title": row["title"],
        "url": row["url"],
        "locations": json.loads(row["locations"]),
        "remote": None if row["remote"] is None else bool(row["remote"]),
        "department": row["department"],
        "employment_type": row["employment_type"],
        "published_at": row["published_at"],
    }

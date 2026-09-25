"""User-owned state: triage decisions, applications and tasks.

Everything here runs only on an explicit user command, and every change
writes a `user` history row. Nothing in this module sends anything anywhere:
recording `applied` means the user says they applied.
"""

import json
import re
import secrets
from datetime import date

from . import boards as boards_mod
from . import db

STATUSES = {
    "to_apply": "To apply",
    "applied": "Applied",
    "screening": "Screening / online assessment",
    "interviewing": "Interviewing",
    "offer": "Offer",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
    "not_applying": "Not applying",
}
ACTIVE = ["to_apply", "applied", "screening", "interviewing", "offer"]
TASK_KINDS = {
    "cv",
    "cover_letter",
    "referral",
    "assessment",
    "interview_prep",
    "follow_up",
    "other",
}


class UserError(ValueError):
    pass


def _history(
    conn, at, *, field, old=None, new=None, posting_id=None, application_id=None, note=None
):
    conn.execute(
        "INSERT INTO history (at, actor_type, posting_id, application_id, field, old, new, note)"
        " VALUES (?, 'user', ?, ?, ?, ?, ?, ?)",
        (at, posting_id, application_id, field, old, new, note),
    )


def check_date(value: str | None, name: str = "date") -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        raise UserError(f"{name} must be YYYY-MM-DD, got {value!r}") from None


def find_posting(conn, ref: str):
    row = conn.execute("SELECT * FROM postings WHERE short_id = ? OR id = ?", (ref, ref)).fetchone()
    if row is None:
        raise UserError(f"no posting '{ref}'; ids look like p-1a2b3c4 (see `job-radar inbox`)")
    return row


def find_application(conn, ref: str):
    row = conn.execute("SELECT * FROM applications WHERE id = ?", (ref,)).fetchone()
    if row is None:
        raise UserError(f"no application '{ref}'; `job-radar queue --all` lists them")
    return row


def _new_app_id(conn) -> str:
    while True:
        candidate = "a-" + secrets.token_hex(3)
        if not conn.execute("SELECT 1 FROM applications WHERE id = ?", (candidate,)).fetchone():
            return candidate


def promote(session, ref: str, *, note: str | None = None) -> str:
    conn = session.conn
    posting = find_posting(conn, ref)
    existing = conn.execute(
        "SELECT application_id FROM triage WHERE posting_id = ? AND decision = 'promoted'",
        (posting["id"],),
    ).fetchone()
    if existing:
        raise UserError(f"already in your queue as {existing[0]}")
    now = session.now_iso
    app_id = _new_app_id(conn)
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO applications (id, posting_id, company, title, url, status, notes,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'to_apply', ?, ?, ?)",
            (app_id, posting["id"], posting["company"], posting["title"], posting["url"], note,
             now, now),
        )  # fmt: skip
        conn.execute(
            "INSERT OR REPLACE INTO triage (posting_id, decision, note, at, application_id)"
            " VALUES (?, 'promoted', ?, ?, ?)",
            (posting["id"], note, now, app_id),
        )
        _history(conn, now, field="triage", new="promoted", posting_id=posting["id"],
                 application_id=app_id, note=note)  # fmt: skip
    return app_id


def decide(session, ref: str, decision: str, *, note: str | None = None) -> None:
    """Dismiss a posting, or keep it for later. A promoted posting's decision
    is changed through its application, not here."""
    if decision not in {"dismissed", "later"}:
        raise UserError(f"unknown decision {decision!r}")
    conn = session.conn
    posting = find_posting(conn, ref)
    current = conn.execute(
        "SELECT decision, application_id FROM triage WHERE posting_id = ?", (posting["id"],)
    ).fetchone()
    if current and current["decision"] == "promoted":
        raise UserError(
            f"already promoted to {current['application_id']}; use "
            f"`job-radar app status {current['application_id']} not_applying` instead"
        )
    with db.transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO triage (posting_id, decision, note, at) VALUES (?, ?, ?, ?)",
            (posting["id"], decision, note, session.now_iso),
        )
        _history(conn, session.now_iso, field="triage",
                 old=current["decision"] if current else None, new=decision,
                 posting_id=posting["id"], note=note)  # fmt: skip


def undo_decision(session, ref: str) -> None:
    conn = session.conn
    posting = find_posting(conn, ref)
    current = conn.execute(
        "SELECT decision FROM triage WHERE posting_id = ?", (posting["id"],)
    ).fetchone()
    if not current:
        raise UserError("no decision recorded for this posting")
    if current["decision"] == "promoted":
        raise UserError("a promoted posting stays linked to its application")
    with db.transaction(conn):
        conn.execute("DELETE FROM triage WHERE posting_id = ?", (posting["id"],))
        _history(conn, session.now_iso, field="triage", old=current["decision"], new=None,
                 posting_id=posting["id"], note="decision undone")  # fmt: skip


def add_manual(session, *, company, title, url=None, status="to_apply", note=None) -> str:
    """An application with no collected posting (a referral, a site the radar
    does not read)."""
    if status not in STATUSES:
        raise UserError(f"unknown status {status!r}; choose from {', '.join(STATUSES)}")
    if not company.strip() or not title.strip():
        raise UserError("company and title are required")
    if url and not re.match(r"^https?://", url):
        raise UserError("url must start with http:// or https://")
    conn = session.conn
    now = session.now_iso
    app_id = _new_app_id(conn)
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO applications (id, company, title, url, status, notes, applied_at,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (app_id, company.strip(), title.strip(), url, status, note,
             session.today if status != "to_apply" else None, now, now),
        )  # fmt: skip
        _history(conn, now, field="application", new=status, application_id=app_id,
                 note="added by hand")  # fmt: skip
    return app_id


def set_status(
    session, app_id: str, status: str, *, note: str | None = None, on: str | None = None
):
    if status not in STATUSES:
        raise UserError(f"unknown status {status!r}; choose from {', '.join(STATUSES)}")
    conn = session.conn
    app = find_application(conn, app_id)
    when = check_date(on, "--on") or session.today
    with db.transaction(conn):
        applied_at = app["applied_at"]
        if status == "applied" and not applied_at:
            applied_at = when
        conn.execute(
            "UPDATE applications SET status = ?, applied_at = ?, updated_at = ? WHERE id = ?",
            (status, applied_at, session.now_iso, app_id),
        )
        _history(conn, session.now_iso, field="status", old=app["status"], new=status,
                 application_id=app_id, posting_id=app["posting_id"],
                 note=(note or "") + ("" if on is None else f" (on {when})"))  # fmt: skip


def set_next(session, app_id: str, text: str | None, *, due: str | None = None) -> None:
    conn = session.conn
    app = find_application(conn, app_id)
    due = check_date(due, "--due")
    with db.transaction(conn):
        conn.execute(
            "UPDATE applications SET next_action = ?, next_due = ?, updated_at = ? WHERE id = ?",
            (text, due, session.now_iso, app_id),
        )
        _history(conn, session.now_iso, field="next_action", old=app["next_action"],
                 new=text if not due else f"{text} (due {due})", application_id=app_id)  # fmt: skip


def add_note(session, app_id: str, text: str) -> None:
    conn = session.conn
    app = find_application(conn, app_id)
    notes = (app["notes"] + "\n" if app["notes"] else "") + f"[{session.today}] {text}"
    with db.transaction(conn):
        conn.execute(
            "UPDATE applications SET notes = ?, updated_at = ? WHERE id = ?",
            (notes, session.now_iso, app_id),
        )
        _history(conn, session.now_iso, field="note", new=text, application_id=app_id)


def add_task(
    session, app_id: str, text: str, *, kind: str = "other", due: str | None = None
) -> int:
    if kind not in TASK_KINDS:
        raise UserError(f"unknown task kind {kind!r}; choose from {', '.join(sorted(TASK_KINDS))}")
    conn = session.conn
    find_application(conn, app_id)
    due = check_date(due, "--due")
    with db.transaction(conn):
        task_id = conn.execute(
            "INSERT INTO tasks (application_id, text, kind, due, created_at) VALUES (?, ?, ?, ?, ?)",
            (app_id, text, kind, due, session.now_iso),
        ).lastrowid
        _history(conn, session.now_iso, field="task", new=text, application_id=app_id,
                 note=f"task {task_id} added")  # fmt: skip
    return task_id


def finish_task(session, task_id: int) -> None:
    conn = session.conn
    task = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise UserError(f"no task {task_id}")
    if task["done_at"]:
        raise UserError(f"task {task_id} is already done")
    with db.transaction(conn):
        conn.execute("UPDATE tasks SET done_at = ? WHERE id = ?", (session.now_iso, task_id))
        _history(conn, session.now_iso, field="task", old=task["text"], new="done",
                 application_id=task["application_id"], note=f"task {task_id} done")  # fmt: skip


# --- boards the user adds or switches off ---------------------------------------------


def add_board(session, *, url=None, ats=None, token=None, company, region=None, kind=None,
              board_id=None, synthetic=False) -> str:  # fmt: skip
    if url:
        ats, token, region = boards_mod.parse_board_url(url)
    if not ats or not token:
        raise UserError("give --url, or both --ats and --token")
    board = boards_mod.Board(
        id=board_id or boards_mod.slug(company), company=company.strip(), ats=ats, token=token,
        kind=kind, region=region, origin="workspace", synthetic=synthetic,
    )  # fmt: skip
    errors = boards_mod.validate_board(board)
    if errors:
        raise UserError("; ".join(errors))
    conn = session.conn
    if any(b.id == board.id for b in boards_mod.all_boards(conn)):
        raise UserError(f"a board '{board.id}' already exists; pass --id to choose another")
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO local_boards (id, company, ats, token, region, kind, careers, added_at,"
            " synthetic) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (board.id, board.company, ats, token, region, kind, url, session.now_iso,
             int(synthetic)),
        )  # fmt: skip
    return board.id


def set_board_enabled(session, board_id: str, enabled: bool, note: str | None = None) -> None:
    boards_mod.get(session.conn, board_id)
    with db.transaction(session.conn):
        session.conn.execute(
            "INSERT OR REPLACE INTO board_settings (board_id, enabled, note, at) VALUES (?, ?, ?, ?)",
            (board_id, int(enabled), note, session.now_iso),
        )


def application_rows(conn, *, active_only: bool):
    rows = conn.execute(
        "SELECT a.*, p.state AS posting_state, p.missing_since, p.short_id, p.locations"
        " FROM applications a LEFT JOIN postings p ON p.id = a.posting_id"
    ).fetchall()
    out = []
    for row in rows:
        if active_only and row["status"] not in ACTIVE:
            continue
        item = dict(row)
        item["locations"] = json.loads(row["locations"]) if row["locations"] else []
        item["open_tasks"] = [
            dict(t)
            for t in conn.execute(
                "SELECT * FROM tasks WHERE application_id = ? AND done_at IS NULL ORDER BY"
                " COALESCE(due, '9999'), id",
                (row["id"],),
            )
        ]
        out.append(item)
    return out


def set_deadline(session, app_id: str, deadline: str | None, *, note: str | None = None) -> None:
    """Record the date by which you must apply (your own reading)."""
    conn = session.conn
    app = find_application(conn, app_id)
    deadline = check_date(deadline, "deadline") if deadline else None
    with db.transaction(conn):
        conn.execute(
            "UPDATE applications SET deadline = ?, deadline_basis = ?, updated_at = ? WHERE id = ?",
            (deadline, "user_reported" if deadline else None, session.now_iso, app_id),
        )
        _history(conn, session.now_iso, field="deadline", old=app["deadline"], new=deadline,
                 application_id=app_id, note=note)  # fmt: skip


def track_window(session, window_id: str, *, note: str | None = None) -> str:
    """Put a hiring window in your queue. Its closing date, if one is known,
    becomes the application deadline with the same basis, so a reported date
    stays labelled as reported."""
    from . import windows

    conn = session.conn
    window = windows.get(conn, window_id)
    existing = conn.execute(
        "SELECT id FROM applications WHERE window_id = ?", (window_id,)
    ).fetchone()
    if existing:
        raise UserError(f"already in your queue as {existing[0]}")
    now = session.now_iso
    app_id = _new_app_id(conn)
    closes = window.closes if window.closes.dated else None
    with db.transaction(conn):
        conn.execute(
            "INSERT INTO applications (id, company, title, url, status, notes, deadline,"
            " deadline_basis, window_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, 'to_apply', ?, ?, ?, ?, ?, ?)",
            (app_id, window.company, window.programme, window.page, note,
             closes.date if closes else None, closes.basis if closes else None, window_id,
             now, now),
        )  # fmt: skip
        _history(conn, now, field="application", new="to_apply", application_id=app_id,
                 note=f"from window {window_id}")  # fmt: skip
    return app_id

"""Read-only views: the discovery inbox, one posting, the queue and exports."""

import csv
import io
import json
from datetime import date, datetime, timedelta

from . import applications, locations, screening

INBOX_VIEWS = {
    "relevant": "matches your preferences or needs a check; not yet decided",
    "new": "first seen in the last 7 days; matches or needs a check; not yet decided",
    "later": "kept for later",
    "dismissed": "dismissed",
    "outside": "ruled out by your preferences, with the reason (audit)",
    "missing": "no longer listed on its board",
    "all": "every stored posting",
}


def _posting_dict(row) -> dict:
    item = dict(row)
    item["locations"] = json.loads(row["locations"])
    item["remote"] = None if row["remote"] is None else bool(row["remote"])
    return item


def inbox(session, *, view="relevant", country=None, family=None, company=None, search=None,
          limit=None) -> list[dict]:  # fmt: skip
    if view not in INBOX_VIEWS:
        raise applications.UserError(f"unknown view {view!r}; choose from {', '.join(INBOX_VIEWS)}")
    prefs = session.preferences()
    conn = session.conn
    rows = conn.execute(
        "SELECT p.*, t.decision, t.application_id FROM postings p"
        " LEFT JOIN triage t ON t.posting_id = p.id ORDER BY p.first_seen_at DESC, p.company, p.title"
    ).fetchall()
    week_ago = (session.clock.now() - timedelta(days=7)).isoformat()
    code = locations.normalise_code(country) if country else None
    out = []
    for row in rows:
        item = _posting_dict(row)
        verdict = screening.screen(item, prefs)
        item["verdict"] = verdict
        decided = row["decision"]
        listed = row["state"] == "listed"
        wanted = verdict.status in {"relevant", "check"}
        keep = {
            "relevant": listed and wanted and not decided,
            "new": listed and wanted and not decided and _after(row["first_seen_at"], week_ago),
            "later": decided == "later",
            "dismissed": decided == "dismissed",
            "outside": listed and verdict.status == "outside" and not decided,
            "missing": not listed,
            "all": True,
        }[view]
        if not keep:
            continue
        if code and code not in verdict.countries:
            continue
        if family and family not in verdict.families:
            continue
        if company and company.casefold() not in row["company"].casefold():
            continue
        if (
            search
            and search.casefold()
            not in " ".join(
                [row["title"], row["company"], *item["locations"], row["department"] or ""]
            ).casefold()
        ):
            continue
        out.append(item)
    return out[:limit] if limit else out


def _after(stamp: str, threshold_iso: str) -> bool:
    try:
        a = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        b = datetime.fromisoformat(threshold_iso)
    except ValueError:
        return False
    return a >= b


def posting_detail(session, ref: str) -> dict:
    conn = session.conn
    row = applications.find_posting(conn, ref)
    item = _posting_dict(row)
    item["verdict"] = screening.screen(item, session.preferences())
    triage = conn.execute("SELECT * FROM triage WHERE posting_id = ?", (row["id"],)).fetchone()
    item["triage"] = dict(triage) if triage else None
    item["history"] = [
        dict(h)
        for h in conn.execute(
            "SELECT * FROM history WHERE posting_id = ? ORDER BY id", (row["id"],)
        )
    ]
    item["snapshots"] = conn.execute(
        "SELECT COUNT(*) FROM snapshots WHERE posting_id = ?", (row["id"],)
    ).fetchone()[0]
    return item


def queue(session, *, show_all=False) -> list[dict]:
    rows = applications.application_rows(session.conn, active_only=not show_all)
    today = session.today
    for row in rows:
        due_dates = [row["next_due"], row["deadline"]] + [t["due"] for t in row["open_tasks"]]
        due_dates = [d for d in due_dates if d]
        row["earliest_due"] = min(due_dates) if due_dates else None
        row["overdue"] = bool(row["earliest_due"] and row["earliest_due"] < today)
        row["due_soon"] = bool(
            row["earliest_due"]
            and not row["overdue"]
            and date.fromisoformat(row["earliest_due"]) - date.fromisoformat(today)
            <= timedelta(days=3)
        )
    order = {s: i for i, s in enumerate(applications.STATUSES)}
    rows.sort(key=lambda r: (r["earliest_due"] or "9999", order[r["status"]], r["company"]))
    return rows


# --- exports --------------------------------------------------------------------------

APP_FIELDS = ["id", "deadline", "deadline_basis", "company", "title", "status", "applied_at", "next_action", "next_due", "url",
              "posting_id", "posting_state", "notes", "created_at", "updated_at"]  # fmt: skip
POSTING_FIELDS = ["short_id", "company", "title", "locations", "countries", "families",
                  "seniority", "screen", "reasons", "url", "state", "first_seen_at"]  # fmt: skip


def export_data(session) -> dict:
    apps = applications.application_rows(session.conn, active_only=False)
    postings = inbox(session, view="relevant")
    return {
        "exported_at": session.now_iso,
        "workspace_kind": session.kind,
        "applications": [{k: a.get(k) for k in APP_FIELDS} for a in apps],
        "relevant_postings": [_posting_export(p) for p in postings],
    }


def _posting_export(p: dict) -> dict:
    v = p["verdict"]
    return {
        "short_id": p["short_id"],
        "company": p["company"],
        "title": p["title"],
        "locations": "; ".join(p["locations"]),
        "countries": " ".join(v.countries),
        "families": " ".join(v.families),
        "seniority": v.seniority,
        "screen": v.status,
        "reasons": "; ".join(v.reasons),
        "url": p["url"],
        "state": p["state"],
        "first_seen_at": p["first_seen_at"],
    }


def to_csv(rows: list[dict], fields: list[str]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: "" if row.get(k) is None else row.get(k) for k in fields})
    return buffer.getvalue()

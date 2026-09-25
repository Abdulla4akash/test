import sqlite3

import pytest

from eu_job_radar import applications, db, windows
from eu_job_radar.fetch import RawResponse
from eu_job_radar.windows import WindowError


def test_shipped_windows_are_honest():
    shipped = windows.shipped()
    assert len(shipped) >= 20
    for w in shipped:
        # Written without internet: nothing may claim to be announced yet.
        assert not w.opens.fixed and not w.closes.fixed
        assert w.page.startswith("https://")
    google = next(w for w in shipped if w.id == "google-swe-intern")
    assert google.closes.basis == "owner_reported" and google.closes.date == "2026-10-07"
    assert "not checked" in google.closes.describe()


def test_announcement_needs_wording():
    bad = '[[windows]]\nid = "x"\ncompany = "X"\nprogramme = "P"\nkind = "internship"\n' \
          'page = "https://example.org"\n[windows.closes]\nbasis = "current_cycle_announcement"\n' \
          'date = "2026-11-01"\n'  # fmt: skip
    with pytest.raises(WindowError, match="wording"):
        windows.shipped(bad)


def test_dated_basis_needs_a_date():
    bad = '[[windows]]\nid = "x"\ncompany = "X"\nprogramme = "P"\nkind = "internship"\n' \
          'page = "https://example.org"\n[windows.closes]\nbasis = "owner_reported"\n'  # fmt: skip
    with pytest.raises(WindowError, match="needs a date"):
        windows.shipped(bad)


def test_report_on_shipped_window_and_new_window(session):
    windows.report(
        session, "jane-street-intern", closes="2026-11-01", source="https://example.org/js"
    )
    w = windows.get(session.conn, "jane-street-intern")
    assert w.closes.basis == "user_reported" and w.closes.date == "2026-11-01"
    with pytest.raises(WindowError, match="needs --company"):
        windows.report(session, "acme-intern", closes="2026-12-01")
    windows.report(session, "acme-intern", closes="2026-12-01", company="Acme",
                   programme="SWE Intern", page="https://example.org/acme")  # fmt: skip
    assert windows.get(session.conn, "acme-intern").origin == "workspace"
    with pytest.raises(WindowError, match="YYYY-MM-DD"):
        windows.report(session, "jane-street-intern", closes="7 Oct")


def test_deadlines_soonest_first_and_labelled(session):
    windows.report(session, "optiver-intern", closes="2026-10-25")
    rows = windows.deadlines(session, days=60)
    # Test clock is 2026-10-20: Google's 7 October is 13 days past, so not shown.
    assert [r["ref"] for r in rows] == ["optiver-intern"]
    assert rows[0]["days_left"] == 5 and rows[0]["basis"] == "user_reported"


def test_track_window_carries_the_basis_and_shows_once(session):
    windows.report(session, "imc-intern", closes="2026-10-30")
    app_id = applications.track_window(session, "imc-intern")
    row = session.conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    assert row["deadline"] == "2026-10-30" and row["deadline_basis"] == "user_reported"
    rows = windows.deadlines(session)
    assert [r["event"] for r in rows] == ["application deadline"]
    with pytest.raises(applications.UserError, match="already"):
        applications.track_window(session, "imc-intern")


def test_app_deadline(session):
    app = applications.add_manual(session, company="Co", title="SWE")
    applications.set_deadline(session, app, "2026-10-22")
    assert windows.deadlines(session)[0]["days_left"] == 2


def test_check_pages_compares_wording_only(session, net):
    net.responses["https://www.janestreet.com/join-jane-street/"] = RawResponse(
        200, {"content-type": "text/html"}, b"<html><p>Apply now</p></html>"
    )
    results = windows.check_pages(session, ["jane-street-intern"])
    assert results[0].status == "reachable"
    assert windows.get(session.conn, "jane-street-intern").closes.basis == "not_yet_verified"


def test_migration_from_schema_1(tmp_path, clock):
    from eu_job_radar.workspace import init_workspace, open_session

    root = init_workspace(tmp_path / "old", clock=clock)
    conn = sqlite3.connect(root / "radar.sqlite3")
    conn.executescript(
        "DROP TABLE local_windows; CREATE TABLE a2 AS SELECT id, posting_id, company, title, url,"
        " status, next_action, next_due, notes, applied_at, created_at, updated_at FROM applications;"
        " DROP TABLE applications; ALTER TABLE a2 RENAME TO applications;"
        " UPDATE meta SET value = '1' WHERE key = 'schema_version';"
    )
    conn.close()
    session = open_session(root, clock=clock)
    assert db.meta(session.conn, "schema_version") == str(db.SCHEMA_VERSION)
    cols = {r[1] for r in session.conn.execute("PRAGMA table_info(applications)")}
    assert {"deadline", "deadline_basis", "window_id"} <= cols
    applications.add_manual(session, company="Co", title="SWE")
    session.close()

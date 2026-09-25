import sqlite3
from copy import deepcopy

import pytest

from eu_job_radar import applications, db, windows
from eu_job_radar.fetch import RawResponse
from eu_job_radar.windows import WindowError


@pytest.fixture(autouse=True)
def synthetic_windows(monkeypatch, request):
    if request.node.name == "test_shipped_windows_are_honest":
        return
    original = windows.shipped
    samples = [
        windows.Window(
            key,
            "Example Company",
            "Engineering internship",
            "internship",
            "2027",
            f"https://example.org/{key}",
        )
        for key in ("example-intern", "fjord-intern", "kite-intern", "bolt-intern")
    ]
    samples[0].closes = windows.Claim(basis="owner_reported", date="2026-10-07")
    monkeypatch.setattr(
        windows,
        "shipped",
        lambda text=None: original(text) if text is not None else deepcopy(samples),
    )


def test_shipped_windows_are_honest():
    shipped = windows.shipped()
    assert len(shipped) >= 20
    for w in shipped:
        assert w.page.startswith("https://")
        for claim in (w.opens, w.closes):
            if claim.fixed:
                assert claim.wording and claim.source and claim.checked_on
            if claim.basis == "not_yet_verified":
                assert claim.date is None


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
    windows.report(session, "fjord-intern", closes="2026-11-01", source="https://example.org/js")
    w = windows.get(session.conn, "fjord-intern")
    assert w.closes.basis == "user_reported" and w.closes.date == "2026-11-01"
    with pytest.raises(WindowError, match="needs --company"):
        windows.report(session, "acme-intern", closes="2026-12-01")
    windows.report(session, "acme-intern", closes="2026-12-01", company="Acme",
                   programme="SWE Intern", page="https://example.org/acme")  # fmt: skip
    assert windows.get(session.conn, "acme-intern").origin == "workspace"
    with pytest.raises(WindowError, match="YYYY-MM-DD"):
        windows.report(session, "fjord-intern", closes="7 Oct")


def test_deadlines_soonest_first_and_labelled(session):
    windows.report(session, "kite-intern", closes="2026-10-25")
    rows = windows.deadlines(session, days=60)
    # The synthetic owner's date is 13 days past, so not shown.
    assert [r["ref"] for r in rows] == ["kite-intern"]
    assert rows[0]["days_left"] == 5 and rows[0]["basis"] == "user_reported"


def test_track_window_carries_the_basis_and_shows_once(session):
    windows.report(session, "bolt-intern", closes="2026-10-30")
    app_id = applications.track_window(session, "bolt-intern")
    row = session.conn.execute("SELECT * FROM applications WHERE id = ?", (app_id,)).fetchone()
    assert row["deadline"] == "2026-10-30" and row["deadline_basis"] == "user_reported"
    rows = windows.deadlines(session)
    assert [r["event"] for r in rows] == ["application deadline"]
    with pytest.raises(applications.UserError, match="already"):
        applications.track_window(session, "bolt-intern")


def test_app_deadline(session):
    app = applications.add_manual(session, company="Co", title="SWE")
    applications.set_deadline(session, app, "2026-10-22")
    assert windows.deadlines(session)[0]["days_left"] == 2


def test_check_pages_compares_wording_only(session, net):
    net.responses["https://example.org/fjord-intern"] = RawResponse(
        200, {"content-type": "text/html"}, b"<html><p>Apply now</p></html>"
    )
    results = windows.check_pages(session, ["fjord-intern"])
    assert results[0].status == "reachable"
    assert windows.get(session.conn, "fjord-intern").closes.basis == "not_yet_verified"


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


def test_announcements_require_source_and_check_date():
    text = """[[windows]]
id = "example"
company = "Acme"
programme = "Engineering internship"
page = "https://example.org/programme"
[windows.closes]
basis = "current_cycle_announcement"
date = "2026-11-01"
wording = "Apply before November 1, 2026."
"""
    with pytest.raises(WindowError, match="source and checked_on"):
        windows.shipped(text)
    valid = text + 'source = "https://example.org/announcement"\nchecked_on = "2026-10-20"\n'
    assert windows.shipped(valid)[0].closes.fixed


def test_reports_preserve_other_dates_and_local_metadata(session):
    windows.report(
        session,
        "fjord-intern",
        closes="2026-11-01",
        time="17:00",
        timezone="UTC",
        source="https://example.org/announcement",
        note="Read by me",
    )
    windows.report(session, "fjord-intern", opens="2026-10-01")
    w = windows.get(session.conn, "fjord-intern")
    assert w.closes.date == "2026-11-01" and w.opens.date == "2026-10-01"
    assert w.closes.time == "17:00" and w.closes.source == "https://example.org/announcement"
    windows.report(
        session,
        "local-intern",
        closes="2026-12-01",
        company="Acme",
        programme="Engineer",
        page="https://example.org/local",
    )
    windows.report(session, "local-intern", opens="2026-11-01")
    assert windows.get(session.conn, "local-intern").company == "Acme"
    assert windows.get(session.conn, "local-intern").closes.date == "2026-12-01"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"closes": "2026-11-01", "time": "99:99"},
        {"closes": "2026-11-01", "time": "24:00"},
        {"closes": "20261101"},
        {"closes": "2026-11-01", "opens": "2026-11-02"},
        {"opens": "2026-11-01", "time": "17:00"},
    ],
)
def test_report_rejects_invalid_dates_and_times(session, kwargs):
    with pytest.raises(WindowError):
        windows.report(session, "fjord-intern", **kwargs)


def test_old_cycle_report_does_not_supply_current_dates(session):
    windows.report(session, "fjord-intern", closes="2026-11-01")
    session.conn.execute("UPDATE local_windows SET cycle = '2026'")
    assert not windows.get(session.conn, "fjord-intern").closes.dated


def test_announced_opening_cannot_be_overridden(session, monkeypatch):
    w = windows.get(session.conn, "fjord-intern")
    w.opens = windows.Claim(
        basis="current_cycle_announcement",
        date="2026-10-01",
        wording="Opens October 1.",
        source="https://example.org/open",
        checked_on="2026-09-25",
    )
    monkeypatch.setattr(windows, "shipped", lambda: [w])
    with pytest.raises(WindowError, match="announced opening"):
        windows.report(session, w.id, opens="2026-10-02")


def test_updated_official_date_is_not_hidden_by_tracked_report(session, monkeypatch):
    windows.report(session, "fjord-intern", closes="2026-11-01")
    applications.track_window(session, "fjord-intern")
    w = windows.get(session.conn, "fjord-intern")
    w.closes = windows.Claim(
        basis="current_cycle_announcement",
        date="2026-11-03",
        wording="Apply before November 3.",
        source="https://example.org/close",
        checked_on="2026-10-20",
    )
    monkeypatch.setattr(windows, "shipped", lambda: [w])
    rows = windows.deadlines(session)
    assert {r["date"] for r in rows} == {"2026-11-01", "2026-11-03"}
    assert {r["basis"] for r in rows} == {"user_reported", "current_cycle_announcement"}


def test_page_check_uses_claim_source_and_decodes_entities(session, net, monkeypatch):
    w = windows.get(session.conn, "fjord-intern")
    w.closes = windows.Claim(
        basis="current_cycle_announcement",
        date="2026-11-01",
        wording="Apply & upload before November 1.",
        source="https://example.org/announcement",
        checked_on="2026-10-20",
    )
    monkeypatch.setattr(windows, "shipped", lambda: [w])
    net.responses[w.closes.source] = RawResponse(
        200, {"content-type": "text/html"}, b"<p>Apply &amp; upload before <b>November 1.</b></p>"
    )
    assert windows.check_pages(session)[0].status == "present"
    assert w.page not in net.sent


def test_demo_window_check_and_invalid_budgets_make_no_requests(session, net):
    with pytest.raises(WindowError, match="budget"):
        windows.check_pages(session, budget=0)
    with pytest.raises(WindowError, match="--days"):
        windows.deadlines(session, days=-1)
    session.kind = "demo"
    with pytest.raises(WindowError, match="demo"):
        windows.check_pages(session)
    assert net.sent == []

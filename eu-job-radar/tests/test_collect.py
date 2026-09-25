import sqlite3

import pytest
from conftest import ASHBY, GH, LEVER, count, gh_board, gh_job

from eu_job_radar import applications, collect, db, views

IDS = ["acme", "bolt-co", "kite"]


def script_all(net, gh=None):
    net.json(GH, gh or gh_board(gh_job(1, "ML Engineer"), gh_job(2, "Backend Engineer", "London")))
    net.json(LEVER, [{"id": "l-1", "text": "Graduate Software Engineer",
                      "categories": {"location": "Stockholm"}}])  # fmt: skip
    net.json(ASHBY, {"jobs": [{"id": "k-1", "title": "Research Engineer", "location": "Zurich"}]})


def test_collects_and_counts(session, net):
    script_all(net)
    summary = collect.run(session, IDS)
    assert [r.outcome for r in summary.results] == ["complete", "complete", "complete"]
    assert count(session.conn, "postings") == 4
    assert count(session.conn, "fetch_log", "purpose = 'robots'") == 3
    assert count(session.conn, "history", "actor_type = 'automated'") == 4


def test_unchanged_recollection_adds_nothing(session, net, clock):
    script_all(net)
    collect.run(session, IDS)
    clock.advance(hours=3)
    summary = collect.run(session, IDS)
    assert all(r.counts["new"] == 0 and r.counts["changed"] == 0 for r in summary.results)
    assert count(session.conn, "snapshots") == 4


def test_change_is_recorded_with_history(session, net, clock):
    script_all(net)
    collect.run(session, ["acme"])
    net.json(GH, gh_board(gh_job(1, "Senior ML Engineer"), gh_job(2, "Backend Engineer", "London")))
    clock.advance(days=1)
    collect.run(session, ["acme"])
    fields = [r["field"] for r in session.conn.execute(
        "SELECT field FROM history WHERE posting_id = 'acme:1' ORDER BY id")]  # fmt: skip
    assert fields == ["posting", "title"]


def test_missing_only_after_a_complete_read_and_reappears(session, net, clock):
    script_all(net)
    collect.run(session, ["acme"])
    # A partial read (the board reports more jobs than it returned) marks nothing.
    net.json(GH, gh_board(gh_job(1, "ML Engineer"), total=5))
    collect.run(session, ["acme"])
    assert count(session.conn, "postings", "state = 'missing'") == 0
    # A failure marks nothing.
    net.status(GH, 500)
    assert collect.run(session, ["acme"]).results[0].outcome == "fetch_failed"
    assert count(session.conn, "postings", "state = 'missing'") == 0
    # A complete read without job 2 marks it missing, never deletes it.
    net.json(GH, gh_board(gh_job(1, "ML Engineer"), total=1))
    collect.run(session, ["acme"])
    assert count(session.conn, "postings", "state = 'missing'") == 1
    assert count(session.conn, "postings") == 2
    # Listed again: back to listed, with history.
    net.json(GH, gh_board(gh_job(1, "ML Engineer"), gh_job(2, "Backend Engineer", "London")))
    summary = collect.run(session, ["acme"])
    assert summary.results[0].counts["reappeared"] == 1
    assert count(session.conn, "postings", "state = 'missing'") == 0


def test_board_not_found(session, net):
    net.status(GH, 404)
    result = collect.run(session, ["acme"]).results[0]
    assert result.outcome == "not_found"
    assert "HTTP 404" in result.detail
    assert collect.health(session.conn)["acme"]["last_outcome"] == "not_found"


def test_html_instead_of_json_is_a_failure_not_empty(session, net):
    from eu_job_radar.fetch import RawResponse

    net.responses[GH] = RawResponse(200, {"content-type": "text/html"}, b"<html>login</html>")
    assert collect.run(session, ["acme"]).results[0].outcome == "fetch_failed"


def test_unusable_items_make_the_read_partial(session, net, clock):
    script_all(net)
    collect.run(session, ["acme"])
    net.json(GH, gh_board(gh_job(1, "ML Engineer"), {"id": None, "title": "broken"}))
    clock.advance(days=1)
    result = collect.run(session, ["acme"]).results[0]
    assert result.outcome == "partial"
    assert count(session.conn, "postings", "state = 'missing'") == 0
    assert count(session.conn, "quarantine") == 1


def test_robots_disallow_refuses_the_board(session, net):
    from eu_job_radar.fetch import RawResponse

    net.responses["https://boards-api.greenhouse.io/robots.txt"] = RawResponse(
        200, {"content-type": "text/plain"}, b"User-agent: *\nDisallow: /v1/\n"
    )
    result = collect.run(session, ["acme"]).results[0]
    assert result.outcome == "fetch_failed" and "robots" in result.detail
    assert GH not in net.sent


def test_budget_stops_the_run(session, net):
    script_all(net)
    summary = collect.run(session, IDS, budget=3)
    assert summary.requests <= 3
    assert summary.results[-1].outcome == "fetch_failed"


def test_one_request_per_second_per_ats(session, net, clock):
    applications.add_board(session, url="https://boards.greenhouse.io/example-two", company="Two")
    script_all(net)
    net.json("https://boards-api.greenhouse.io/v1/boards/example-two/jobs", gh_board())
    times = []
    original = net.send

    def timed(request):
        times.append((request.url, net.now))
        return original(request)

    net.send = timed
    collect.run(session, ["acme", "two"])
    board_times = [t for url, t in times if "/v1/boards/" in url]
    assert board_times[1] - board_times[0] >= 1.0


def test_disabled_board_is_refused(session, net):
    applications.set_board_enabled(session, "acme", False, "wrong token")
    with pytest.raises(collect.CollectError):
        collect.run(session, ["acme"])  # nothing left to collect


def test_unknown_board_is_refused_but_others_run(session, net):
    script_all(net)
    summary = collect.run(session, ["nope", "acme"])
    assert summary.refused == [("nope", "no such board")]
    assert [r.board.id for r in summary.results] == ["acme"]


def test_collection_cannot_write_user_tables(session):
    with pytest.raises(db.UserStateWrite):
        with db.collected_state_only(session.conn):
            session.conn.execute(
                "INSERT INTO applications (id, company, title, status, created_at, updated_at)"
                " VALUES ('a-x', 'c', 't', 'to_apply', 'now', 'now')"
            )
    assert count(session.conn, "applications") == 0


def test_user_state_survives_recollection(session, net, clock):
    script_all(net)
    collect.run(session, ["acme"])
    ml = views.inbox(session, search="ML Engineer")[0]
    app_id = applications.promote(session, ml["short_id"])
    applications.set_status(session, app_id, "interviewing")
    net.json(GH, gh_board(gh_job(2, "Backend Engineer", "London")))
    clock.advance(days=1)
    collect.run(session, ["acme"])
    queue = views.queue(session)
    assert queue[0]["status"] == "interviewing" and queue[0]["posting_state"] == "missing"


def test_second_run_waits_for_the_lock(session, net):
    import fcntl

    handle = open(session.root / "collect.lock", "a+")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(collect.CollectError, match="in progress"):
            collect.run(session, ["acme"])
    finally:
        handle.close()


def test_demo_workspace_refuses_real_boards(tmp_path, clock, net):
    from eu_job_radar.workspace import init_workspace, open_session

    s = open_session(init_workspace(tmp_path / "demo", kind="demo", clock=clock), clock=clock)
    with pytest.raises(collect.CollectError, match="synthetic"):
        collect.run(s, ["anthropic"])
    s.close()


def test_not_sqlite_error_passthrough(session):
    with pytest.raises(sqlite3.OperationalError):
        with db.collected_state_only(session.conn):
            session.conn.execute("SELECT * FROM no_such_table")

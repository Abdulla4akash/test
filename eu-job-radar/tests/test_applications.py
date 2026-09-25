import pytest
from conftest import GH, gh_board, gh_job

from eu_job_radar import applications, collect, views
from eu_job_radar.applications import UserError
from eu_job_radar.cli import main


@pytest.fixture
def stored(session, net):
    net.json(GH, gh_board(
        gh_job(1, "ML Engineer", "Amsterdam"), gh_job(2, "Backend Engineer", "London"),
        gh_job(3, "Recruiter", "London"), gh_job(4, "Software Engineer", "Austin, TX"),
    ))  # fmt: skip
    collect.run(session, ["acme"])
    return {p["title"]: p for p in views.inbox(session, view="all")}


def test_inbox_views(session, stored):
    relevant = [p["title"] for p in views.inbox(session)]
    assert sorted(relevant) == ["Backend Engineer", "ML Engineer"]
    outside = [p["title"] for p in views.inbox(session, view="outside")]
    assert sorted(outside) == ["Recruiter", "Software Engineer"]
    assert [p["title"] for p in views.inbox(session, country="uk")] == ["Backend Engineer"]
    assert [p["title"] for p in views.inbox(session, family="ml")] == ["ML Engineer"]


def test_promote_dismiss_later_undo(session, stored):
    ml, be = stored["ML Engineer"], stored["Backend Engineer"]
    app_id = applications.promote(session, ml["short_id"])
    with pytest.raises(UserError, match="already"):
        applications.promote(session, ml["short_id"])
    with pytest.raises(UserError, match="already promoted"):
        applications.decide(session, ml["short_id"], "dismissed")
    applications.decide(session, be["short_id"], "later")
    assert views.inbox(session) == []
    assert [p["title"] for p in views.inbox(session, view="later")] == ["Backend Engineer"]
    applications.undo_decision(session, be["short_id"])
    assert [p["title"] for p in views.inbox(session)] == ["Backend Engineer"]
    assert views.queue(session)[0]["id"] == app_id


def test_status_next_tasks_and_queue_order(session, stored, clock):
    a = applications.promote(session, stored["ML Engineer"]["short_id"])
    b = applications.add_manual(
        session, company="Referral Co", title="SWE", url="https://example.org/x"
    )
    applications.set_next(session, b, "Send CV", due="2026-10-19")  # yesterday: overdue
    applications.add_task(session, a, "Take-home", kind="assessment", due="2026-10-22")
    applications.set_status(session, a, "applied", on="2026-10-18")
    rows = views.queue(session)
    assert [r["id"] for r in rows] == [b, a]
    assert rows[0]["overdue"] and rows[1]["due_soon"]
    assert rows[1]["applied_at"] == "2026-10-18"
    task_id = rows[1]["open_tasks"][0]["id"]
    applications.finish_task(session, task_id)
    with pytest.raises(UserError):
        applications.finish_task(session, task_id)
    applications.set_status(session, b, "rejected")
    assert [r["id"] for r in views.queue(session)] == [a]
    assert len(views.queue(session, show_all=True)) == 2


def test_bad_inputs(session, stored):
    with pytest.raises(UserError):
        applications.set_status(session, "a-nope", "applied")
    app = applications.promote(session, stored["ML Engineer"]["short_id"])
    with pytest.raises(UserError, match="unknown status"):
        applications.set_status(session, app, "hired")
    with pytest.raises(UserError, match="YYYY-MM-DD"):
        applications.set_next(session, app, "x", due="next week")
    with pytest.raises(UserError, match="already exists"):
        applications.add_board(session, url="https://jobs.lever.co/other", company="Acme")


def test_user_history_rows(session, stored):
    app = applications.promote(session, stored["ML Engineer"]["short_id"])
    applications.set_status(session, app, "applied")
    rows = session.conn.execute(
        "SELECT field, old, new FROM history WHERE actor_type = 'user' AND application_id = ?",
        (app,),
    ).fetchall()
    assert [(r["field"], r["old"], r["new"]) for r in rows] == [
        ("triage", None, "promoted"),
        ("status", "to_apply", "applied"),
    ]


def test_cli_end_to_end(session, stored, capsys, tmp_path):
    ws = ["--workspace", str(session.root)]
    assert main(ws + ["inbox", "--details"]) == 0
    out = capsys.readouterr().out
    assert "ML Engineer" in out and "Recruiter" not in out
    short = stored["ML Engineer"]["short_id"]
    assert main(ws + ["promote", short, "--note", "good team"]) == 0
    app_id = capsys.readouterr().out.split(" as ")[1].split(".")[0]
    assert main(ws + ["app-status", app_id, "applied"]) == 0
    assert main(ws + ["queue"]) == 0
    assert "Applied" in capsys.readouterr().out
    assert main(ws + ["show", short]) == 0
    assert "promoted" in capsys.readouterr().out
    assert main(ws + ["export", "--out", str(tmp_path / "out")]) == 0
    csv_text = next((tmp_path / "out").glob("applications-*.csv")).read_text()
    assert app_id in csv_text
    assert main(ws + ["export", "--format", "json", "--out", str(tmp_path / "out")]) == 0
    assert main(ws + ["boards"]) == 0 and "acme" in capsys.readouterr().out
    assert main(ws + ["runs", "1", "--requests"]) == 0
    assert main(ws + ["validate"]) == 0
    assert main(ws + ["app-status", "a-missing", "applied"]) == 2


def test_cli_init_refuses_existing(tmp_path, capsys):
    target = str(tmp_path / "new")
    assert main(["--workspace", target, "init"]) == 0
    assert main(["--workspace", target, "init"]) == 2
    assert "already holds" in capsys.readouterr().err


def test_demo_passes(tmp_path, capsys):
    from eu_job_radar import demo

    assert demo.run(tmp_path / "demo") == 0
    assert "FAIL" not in capsys.readouterr().out

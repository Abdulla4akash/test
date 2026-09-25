"""Offline, synthetic reproductions of live validation findings."""

import gzip
import json
import sqlite3

import pytest
from conftest import ASHBY, GH, LEVER, count, gh_board, gh_job

from eu_job_radar import applications, collect, db, ingest, locations
from eu_job_radar.adapters import Board, BoardResult
from eu_job_radar.adapters.ashby import AshbyAdapter
from eu_job_radar.fetch import RawResponse


@pytest.mark.parametrize("size,outcome", [(6_000_000, "complete"), (20_000_001, "fetch_failed")])
def test_large_boards_remain_bounded(session, net, size, outcome):
    body = json.dumps(
        {
            "jobs": [
                {
                    "id": "fictional-1",
                    "title": "Software Engineer",
                    "location": "Berlin",
                    "descriptionPlain": "x" * size,
                    "jobUrl": "https://example.org/job",
                }
            ]
        }
    ).encode()
    net.responses[ASHBY] = RawResponse(
        200, {"content-type": "application/json", "content-encoding": "gzip"}, gzip.compress(body)
    )
    result = collect.run(session, ["kite"]).results[0]
    assert result.outcome == outcome
    if outcome == "complete":
        stored = session.conn.execute("SELECT content FROM snapshots").fetchone()[0]
        assert "descriptionPlain" not in stored and len(stored) < 1000
    else:
        assert "response_too_large" in result.detail
        assert count(session.conn, "postings") == 0


@pytest.mark.parametrize(
    "board,url,payload",
    [
        ("acme", GH, {"jobs": [{"id": 1, "title": {"wrong": "type"}}]}),
        ("bolt-co", LEVER, [{"id": "one", "text": "   "}]),
        ("kite", ASHBY, {"jobs": [{"id": "one", "title": ["Software Engineer"]}]}),
    ],
)
def test_malformed_titles_never_become_readable_postings(session, net, board, url, payload):
    net.json(url, payload)
    result = collect.run(session, [board]).results[0]
    assert result.outcome == "partial"
    assert count(session.conn, "postings") == 0


def test_duplicate_ids_cannot_withdraw_an_absent_job(session, net):
    net.json(GH, gh_board(gh_job(1), gh_job(2)))
    collect.run(session, ["acme"])
    net.json(GH, gh_board(gh_job(1), gh_job(1, "Backend Engineer")))
    result = collect.run(session, ["acme"]).results[0]
    assert result.outcome == "partial"
    assert count(session.conn, "postings", "state = 'missing'") == 0


def test_ingest_itself_refuses_incomplete_quarantined_inventory(session, net):
    net.json(GH, gh_board(gh_job(1)))
    collect.run(session, ["acme"])
    result = BoardResult("complete", quarantined=[{"reason": "unreadable item"}])
    ingest.store_check(
        session.conn,
        board=Board("acme", "Acme", "greenhouse", "example-acme"),
        result=result,
        run_id=None,
        now=session.now_iso,
    )
    assert count(session.conn, "postings", "state = 'missing'") == 0


def test_ashby_nested_and_flat_secondary_addresses():
    result = AshbyAdapter().parse(
        {
            "jobs": [
                {
                    "id": "fictional-1",
                    "title": "Research Engineer",
                    "location": "Cambridge",
                    "address": {
                        "postalAddress": {"addressRegion": "Massachusetts", "addressCountry": "USA"}
                    },
                    "secondaryLocations": [
                        {
                            "location": "Cambridge",
                            "address": {"postalAddress": {"addressCountry": "GBR"}},
                        },
                        {"location": "Remote", "address": {"addressCountry": "Netherlands"}},
                    ],
                    "jobUrl": "https://example.org/research",
                    "isListed": True,
                }
            ]
        },
        Board("acme", "Acme", "ashby", "example-acme"),
    )
    place = locations.read_all(result.postings[0]["locations"])
    assert place.countries == {"US", "GB", "NL"}


@pytest.mark.parametrize("timestamp", [1e100, float("inf"), float("nan")])
def test_unrepresentable_lever_timestamp_does_not_abort_collection(session, net, timestamp):
    net.json(
        LEVER,
        [
            {
                "id": "one",
                "text": "Data Engineer",
                "createdAt": timestamp,
                "hostedUrl": "https://example.org/one",
                "categories": {"location": "London"},
            }
        ],
    )
    result = collect.run(session, ["bolt-co"]).results[0]
    assert result.outcome == "complete"
    assert session.conn.execute("SELECT published_at FROM postings").fetchone()[0] is None


def test_nested_collection_guard_remains_active(session):
    with db.collected_state_only(session.conn):
        with db.collected_state_only(session.conn):
            session.conn.execute("SELECT * FROM applications")
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            session.conn.execute("DELETE FROM local_windows")
        with pytest.raises(db.UserStateWrite):
            with db.collected_state_only(session.conn):
                session.conn.execute("DELETE FROM tasks")
        with pytest.raises(sqlite3.OperationalError):
            session.conn.execute("SELECT * FROM missing_table")
    applications.add_manual(session, company="Acme", title="Engineer")


def test_collection_cannot_rename_user_state(session):
    with pytest.raises(db.UserStateWrite):
        with db.collected_state_only(session.conn):
            session.conn.execute("ALTER TABLE applications RENAME TO collected_apps")
    assert count(session.conn, "applications") == 0


@pytest.mark.parametrize("version", ["invalid", "0", "99"])
def test_unsupported_schema_is_a_domain_error(session, version):
    session.conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (version,))
    with pytest.raises(db.SchemaError):
        db.check_schema(session.conn)


def test_populated_v1_migration_preserves_user_state(tmp_path):
    conn = db.connect(tmp_path / "old.sqlite3")
    schema = db.SCHEMA
    for column in ("deadline", "deadline_basis", "window_id"):
        schema = schema.replace(f"    {column} TEXT,\n", "")
    conn.executescript(schema)
    conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO applications (id, company, title, status, notes, created_at, updated_at)"
        " VALUES ('a-example', 'Acme', 'Engineer', 'interviewing', 'Keep this', 'now', 'now')"
    )
    conn.execute(
        "INSERT INTO tasks (application_id, text, kind, created_at)"
        " VALUES ('a-example', 'Prepare', 'other', 'now')"
    )
    db.check_schema(conn)
    db.check_schema(conn)
    app = conn.execute("SELECT * FROM applications").fetchone()
    assert app["notes"] == "Keep this" and app["status"] == "interviewing"
    assert app["deadline"] is None and count(conn, "tasks") == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()


@pytest.mark.parametrize("status", ["not_applying", "withdrawn", "rejected", "interviewing"])
def test_manual_status_does_not_invent_application_date(session, status):
    ref = applications.add_manual(session, company="Acme", title="Engineer", status=status)
    assert applications.find_application(session.conn, ref)["applied_at"] is None

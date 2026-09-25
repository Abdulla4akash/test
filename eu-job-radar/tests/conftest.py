"""Shared test helpers. Tests run offline with a fixed clock and synthetic data."""

from datetime import datetime

import pytest

from eu_job_radar import applications
from eu_job_radar.clock import FixedClock
from eu_job_radar.offline import OfflineNet
from eu_job_radar.workspace import init_workspace, open_session

NOW = datetime.fromisoformat("2026-10-20T09:00:00+01:00")

GH = "https://boards-api.greenhouse.io/v1/boards/example-acme/jobs"
LEVER = "https://api.lever.co/v0/postings/example-bolt?mode=json"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/example-kite"


class LiveNetworkBlocked(AssertionError):
    pass


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Keep every test away from the real workspace and from the network: the
    real transport, resolver and raw sockets all fail loudly."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("EU_JOB_RADAR_WORKSPACE", raising=False)
    monkeypatch.delenv("EU_JOB_RADAR_NOW", raising=False)

    def blocked(*args, **kwargs):
        raise LiveNetworkBlocked("tests must not use the live network")

    import socket

    from eu_job_radar import collect, fetch

    monkeypatch.setattr(fetch.PinnedTransport, "send", blocked)
    monkeypatch.setattr(fetch, "default_resolver", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(collect, "TRANSPORT_FACTORY", None)
    monkeypatch.setattr(collect, "RESOLVER", None)
    monkeypatch.setattr(collect, "TIMER", None)


@pytest.fixture
def clock():
    return FixedClock(NOW)


@pytest.fixture
def net(monkeypatch):
    from eu_job_radar import collect

    fake = OfflineNet()
    monkeypatch.setattr(collect, "TRANSPORT_FACTORY", lambda: fake)
    monkeypatch.setattr(collect, "RESOLVER", fake.resolve)
    monkeypatch.setattr(collect, "TIMER", fake)
    return fake


@pytest.fixture
def session(tmp_path, clock):
    root = init_workspace(tmp_path / "ws", kind="validation", clock=clock)
    s = open_session(root, clock=clock)
    applications.add_board(s, url="https://boards.greenhouse.io/example-acme", company="Acme")
    applications.add_board(s, url="https://jobs.lever.co/example-bolt", company="Bolt Co")
    applications.add_board(s, url="https://jobs.ashbyhq.com/example-kite", company="Kite")
    yield s
    s.close()


def gh_job(job_id, title="Software Engineer", location="Berlin, Germany"):
    return {
        "id": job_id,
        "title": title,
        "location": {"name": location},
        "absolute_url": f"https://acme.example.org/jobs/{job_id}",
        "updated_at": "2026-10-01T10:00:00Z",
    }


def gh_board(*jobs, total=None):
    payload = {"jobs": list(jobs)}
    if total is not None:
        payload["meta"] = {"total": total}
    return payload


def count(conn, table, where="1=1", params=()):
    return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0]

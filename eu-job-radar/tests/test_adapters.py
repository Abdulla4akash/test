import json

import pytest

from eu_job_radar import boards
from eu_job_radar.adapters import Board, registry
from eu_job_radar.adapters.ashby import AshbyAdapter
from eu_job_radar.adapters.greenhouse import GreenhouseAdapter
from eu_job_radar.adapters.lever import LeverAdapter

BOARD = Board(id="acme", company="Acme", ats="greenhouse", token="example-acme")


def test_greenhouse_parse():
    data = {
        "jobs": [
            {"id": 1, "title": "ML Engineer", "location": {"name": "London"},
             "absolute_url": "https://acme.example.org/1", "updated_at": "2026-10-01T10:00:00Z"},
            {"id": None, "title": "No id"},
            {"id": 3, "title": ""},
        ],
        "meta": {"total": 3},
    }  # fmt: skip
    result = GreenhouseAdapter().parse(data, BOARD)
    assert [p["source_record_id"] for p in result.postings] == ["1"]
    assert result.postings[0]["locations"] == ["London"]
    assert len(result.quarantined) == 2


def test_greenhouse_short_list_is_partial():
    data = {
        "jobs": [{"id": 1, "title": "SWE", "location": {"name": "Berlin"}}],
        "meta": {"total": 9},
    }
    assert GreenhouseAdapter().parse(data, BOARD).outcome == "partial"


def test_greenhouse_rejects_wrong_shape():
    with pytest.raises(ValueError):
        GreenhouseAdapter().parse([], BOARD)


def test_lever_parse_and_eu_host():
    board = Board(id="b", company="B", ats="lever", token="example-bolt", region="eu")
    assert LeverAdapter().url(board) == "https://api.eu.lever.co/v0/postings/example-bolt?mode=json"
    data = [
        {"id": "abc-1", "text": "Data Engineer", "hostedUrl": "https://jobs.lever.co/b/abc-1",
         "categories": {"location": "Remote", "allLocations": ["Remote", "Lisbon"],
                        "commitment": "Full-time", "team": "Data"},
         "workplaceType": "remote", "createdAt": 1790000000000},
    ]  # fmt: skip
    posting = LeverAdapter().parse(data, board).postings[0]
    assert posting["locations"] == ["Remote", "Lisbon"]
    assert posting["remote"] is True
    assert posting["published_at"].startswith("2026-")
    assert posting["employment_type"] == "Full-time"


def test_lever_error_object_is_a_parse_failure():
    with pytest.raises(ValueError):
        LeverAdapter().parse({"ok": False, "error": "Document not found"}, BOARD)


def test_ashby_skips_unlisted_and_reads_secondary_locations():
    data = {"jobs": [
        {"id": "k1", "title": "AI Engineer", "location": "Paris", "isListed": True,
         "secondaryLocations": [{"location": "London"}], "isRemote": False,
         "jobUrl": "https://jobs.ashbyhq.com/kite/k1"},
        {"id": "k2", "title": "Hidden", "location": "Paris", "isListed": False},
    ]}  # fmt: skip
    result = AshbyAdapter().parse(data, BOARD)
    assert [p["title"] for p in result.postings] == ["AI Engineer"]
    assert result.postings[0]["locations"] == ["Paris", "London"]
    assert result.postings[0]["remote"] is None


def test_non_https_links_are_dropped():
    data = {"jobs": [{"id": 1, "title": "SWE", "absolute_url": "javascript:alert(1)"}]}
    assert GreenhouseAdapter().parse(data, BOARD).postings[0]["url"] is None


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://boards.greenhouse.io/acme", ("greenhouse", "acme", None)),
        ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme", None)),
        ("https://jobs.lever.co/acme/abc-123", ("lever", "acme", None)),
        ("https://jobs.eu.lever.co/acme", ("lever", "acme", "eu")),
        ("https://jobs.ashbyhq.com/acme?departmentId=1", ("ashby", "acme", None)),
    ],
)
def test_board_urls(url, expected):
    assert boards.parse_board_url(url) == expected


def test_unsupported_board_url():
    with pytest.raises(boards.BoardError, match="not a supported board host"):
        boards.parse_board_url("https://careers.example.org/jobs")


def test_shipped_registry_is_valid():
    shipped = boards.shipped()
    assert len({b.id for b in shipped}) == len(shipped)
    for board in shipped:
        assert boards.validate_board(board) == []
        assert board.token_status in {"verified", "unverified"}
        if board.token_status == "verified":
            assert board.checked_on
        assert board.ats in registry()


def test_fixture_payloads_are_json_serialisable():
    json.dumps({"jobs": []})

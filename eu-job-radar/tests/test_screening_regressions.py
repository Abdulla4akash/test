"""Offline, synthetic reproductions of live validation findings."""

import pytest
from conftest import GH, gh_board, gh_job

from eu_job_radar import cli, collect, locations, screening, views


@pytest.mark.parametrize(
    "text,countries",
    [
        ("Cambridge", set()),
        ("Cambridge, UK", {"GB"}),
        ("Cambridge, MA", {"US"}),
        ("London, ON", {"CA"}),
        ("CA", {"CA"}),
        ("IN", {"IN"}),
        ("IT", {"IT"}),
        ("Dover, DE, United States", {"US"}),
        ("Amsterdam, NL", {"NL"}),
        ("Berlin/London", {"DE", "GB"}),
        ("New York, NY, London", {"US", "GB"}),
        ("Cambridge, MA, London, UK", {"US", "GB"}),
        ("Bellevue, Washington", {"US"}),
        ("Detroit", {"US"}),
    ],
)
def test_location_disambiguation(text, countries):
    assert locations.read(text).countries == countries


@pytest.mark.parametrize(
    "title,status",
    [
        ("Software Engineer, Content Platform", "relevant"),
        ("Backend Engineer, Sales Platform", "relevant"),
        ("Data Engineer, Finance", "relevant"),
        ("Software Engineering Recruiter", "outside"),
        ("AI Sales Engineer", "outside"),
        ("Applied (AI) Value Engineer", "outside"),
        ("Senior Customer Engineer, Enterprise AI", "outside"),
        ("AI Deployment Strategist", "outside"),
        ("AI Designer", "outside"),
        ("AI Strategy Associate", "outside"),
        ("Infrastructure Business Analyst", "outside"),
        ("Machine Learning Research Intern", "relevant"),
        ("ML Fellows Program", "relevant"),
        ("Microservices Engineer", "relevant"),
        ("Core Developer", "relevant"),
        ("Member of Technical Staff, Machine Learning", "relevant"),
        ("Senior Member of Technical Staff", "relevant"),
        ("Principal Member of Technical Staff", "outside"),
    ],
)
def test_role_function_and_level(title, status):
    result = screening.screen(
        {"title": title, "locations": ["London, UK"]}, screening.Preferences()
    )
    assert result.status == status
    assert result.reasons if status == "outside" else result.notes


@pytest.mark.parametrize(
    "texts,countries,status",
    [
        (["Remote - EMEA"], {"GB"}, "relevant"),
        (["Remote - GMT"], {"GB"}, "check"),
        (["Remote - DACH"], {"GB"}, "outside"),
        (["Remote - AMER"], {"GB"}, "outside"),
        (["New York", "Unspecified campus"], {"GB"}, "check"),
        (["New York", "Unspecified campus", "London"], {"GB"}, "relevant"),
    ],
)
def test_uncertain_and_restricted_locations(texts, countries, status):
    result = screening.screen(
        {"title": "Software Engineer", "locations": texts},
        screening.Preferences(countries=countries),
    )
    assert result.status == status


def test_check_inbox_and_zero_limit_through_cli(session, net, capsys):
    net.json(GH, gh_board(gh_job(1, location="Unspecified campus"), gh_job(2)))
    collect.run(session, ["acme"])
    assert cli.main(["--workspace", str(session.root), "inbox", "--view", "check"]) == 0
    output = capsys.readouterr().out
    assert "1 posting(s)" in output and "Unspecified campus" in output
    assert views.inbox(session, limit=0) == []
    assert cli.main(["--workspace", str(session.root), "inbox", "--limit", "-1"]) == 2

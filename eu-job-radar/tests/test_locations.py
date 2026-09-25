import pytest

from eu_job_radar.locations import read, read_all


@pytest.mark.parametrize(
    "text, countries, remote, regions",
    [
        ("London, UK", {"GB"}, False, set()),
        ("Berlin / Remote", {"DE"}, True, set()),
        ("Paris, Île-de-France, France", {"FR"}, False, set()),
        ("Zürich", {"CH"}, False, set()),
        ("Remote - EMEA", set(), True, {"emea"}),
        ("Remote (Europe)", set(), True, {"europe"}),
        ("New York, NY", {"US"}, False, set()),
        ("Cambridge, MA", {"US"}, False, set()),
        ("Cambridge, United Kingdom", {"GB"}, False, set()),
        ("San Francisco, CA; London", {"US", "GB"}, False, set()),
        ("DACH", {"DE", "AT", "CH"}, False, {"europe"}),
        ("Bengaluru", {"IN"}, False, set()),
        ("Kraków, Poland", {"PL"}, False, set()),
        ("München", {"DE"}, False, set()),
    ],
)
def test_reads_countries(text, countries, remote, regions):
    place = read(text)
    assert place.countries == countries
    assert place.remote is remote
    assert place.regions == regions


def test_unreadable_location_stays_unknown():
    place = read("HQ office")
    assert not place.known
    assert place.unread == ["HQ office"]


def test_plain_remote_names_no_region():
    place = read("Remote")
    assert place.remote and not place.known


def test_empty_is_unknown():
    assert not read(None).known
    assert not read("  ").known


def test_read_all_merges():
    place = read_all(["Amsterdam", "Remote - Europe"])
    assert place.countries == {"NL"} and place.remote and place.regions == {"europe"}


def test_lower_case_words_are_not_codes():
    # "in" and "it" must never be read as India or Italy.
    assert read("Work in it anywhere in the office").countries == set()

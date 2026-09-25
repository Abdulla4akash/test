import pytest

from eu_job_radar import screening
from eu_job_radar.screening import Preferences, PreferencesError, load_preferences, screen


@pytest.mark.parametrize(
    "title, family",
    [
        ("Software Engineer, Payments", "swe"),
        ("Software Development Engineer II", "sde"),
        ("Machine Learning Engineer", "ml"),
        ("Senior MLE, Recommendations", "ml"),
        ("LLM Inference Engineer", "ai"),
        ("Research Engineer, Pretraining", "research_eng"),
        ("Member of Technical Staff", "research_eng"),
        ("Site Reliability Engineer", "infra"),
        ("Quantitative Developer", "quant"),
        ("Backend Engineer (Go)", "backend"),
        ("Data Engineer", "data"),
        ("iOS Engineer", "mobile"),
        ("Hardware Verification Engineer", "other_eng"),
    ],
)
def test_primary_family(title, family):
    assert screening.families(title)[0] == family


@pytest.mark.parametrize(
    "title, level",
    [
        ("Software Engineering Intern", "intern"),
        ("Werkstudent Software Engineering", "intern"),
        ("Graduate Software Engineer", "graduate"),
        ("Junior Backend Developer", "graduate"),
        ("Senior ML Engineer", "senior"),
        ("Staff Engineer", "staff"),
        ("Senior Staff Engineer", "staff"),
        ("Engineering Manager, Platform", "manager"),
        ("Software Engineer", "unstated"),
    ],
)
def test_seniority(title, level):
    assert screening.seniority(title) == level


def verdict(title, locs, remote=None, prefs=None):
    return screen({"title": title, "locations": locs, "remote": remote}, prefs or Preferences())


def test_relevant_when_everything_passes():
    v = verdict("Machine Learning Engineer", ["Amsterdam"])
    assert v.status == "relevant" and not v.reasons


def test_sales_engineer_is_not_engineering():
    v = verdict("Sales Engineer", ["London"])
    assert v.status == "outside"
    assert "not an engineering role" in v.reasons[0]


def test_location_outside():
    v = verdict("Software Engineer", ["Seattle, WA"])
    assert v.status == "outside" and "US" in v.reasons[0]


def test_unreadable_location_needs_check_never_matches():
    v = verdict("Software Engineer", ["Main campus"])
    assert v.status == "check"


def test_remote_without_region_needs_check():
    assert verdict("Software Engineer", ["Remote"]).status == "check"


def test_remote_europe_kept_or_dropped_by_preference():
    assert verdict("Software Engineer", ["Remote - Europe"]).status == "relevant"
    prefs = Preferences(remote_europe=False)
    assert verdict("Software Engineer", ["Remote - Europe"], prefs=prefs).status == "outside"


def test_multi_location_with_one_european_office_matches():
    assert verdict("Software Engineer", ["New York, NY", "London, UK"]).status == "relevant"


def test_default_levels_exclude_staff_and_managers():
    assert verdict("Staff Software Engineer", ["Berlin"]).status == "outside"
    assert verdict("Engineering Manager", ["Berlin"]).status == "outside"


def test_excluded_words():
    prefs = Preferences(exclude_title_words=["php"])
    assert verdict("PHP Developer", ["Berlin"], prefs=prefs).status == "outside"


def test_default_preferences_file_loads(tmp_path):
    path = tmp_path / "preferences.toml"
    path.write_text(screening.DEFAULT_PREFERENCES)
    prefs = load_preferences(path)
    assert "GB" in prefs.countries and "ml" in prefs.families and "staff" not in prefs.seniority


def test_typos_are_errors(tmp_path):
    path = tmp_path / "preferences.toml"
    path.write_text('[roles]\nfamilies = ["mlops"]\n[locations]\ncountries = ["XX", "uk"]\n')
    with pytest.raises(PreferencesError) as err:
        load_preferences(path)
    assert "mlops" in str(err.value) and "XX" in str(err.value)


def test_uk_alias(tmp_path):
    path = tmp_path / "preferences.toml"
    path.write_text('[locations]\ncountries = ["UK"]\n')
    assert load_preferences(path).countries == {"GB"}

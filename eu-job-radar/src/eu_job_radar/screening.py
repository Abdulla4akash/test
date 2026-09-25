"""Screening postings against the user's preferences.

The screen is rules only, and every verdict carries its reasons. It sorts a
posting into one of three bins:

* `relevant`: every check passed.
* `check`: nothing ruled it out, but something could not be read (for
  example the location, or a remote posting with no region).
* `outside`: at least one preference rules it out; the reasons say which.

There is no combined score and no guess: a posting whose location cannot be
read is never treated as matching, and a title the rules do not recognise is
never treated as an engineering role. Screening is derived at read time from
the stored posting and the current preferences, so changing a preference
changes the inbox without re-collecting anything.
"""

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import locations

# Role families, most specific first. A title can match several; the first
# match is its primary family. Patterns are matched case-insensitively
# against the title only.
FAMILIES: list[tuple[str, str, str]] = [
    ("sde", "Software Development Engineer", r"\bsoftware development engineer\b|\bsde\b"),
    (
        "research_eng",
        "Research engineer / scientist",
        r"\bresearch (engineer|scientist)\b|\bapplied (research )?scientist\b"
        r"|\bmember of (the )?technical staff\b|\bresearcher\b",
    ),
    (
        "ai",
        "AI / LLM engineer",
        r"\bai\b|\bllms?\b|\bgen(erative)? ?ai\b|\bagents?\b(?=.*\b(engineer|developer)\b)"
        r"|\bfoundation models?\b|\bartificial intelligence\b",
    ),
    (
        "ml",
        "Machine learning engineer",
        r"\bmachine learning\b|\bml\b|\bmle\b|\bdeep learning\b|\bcomputer vision\b|\bnlp\b"
        r"|\bnatural language\b|\brecommend(er|ation)s?\b|\bperception\b|\breinforcement learning\b",
    ),
    (
        "data",
        "Data / MLOps",
        r"\bdata (engineer|scientist|platform|infrastructure)\b|\banalytics engineer\b|\bmlops\b",
    ),
    (
        "quant",
        "Quant developer",
        r"\bquant(itative)? (developer|engineer|researcher|trader|dev)\b|\btrading (systems|platform)\b"
        r"|\blow[- ]latency\b|\balgo(rithmic)? (developer|trading)\b|\bquant\b",
    ),
    ("security", "Security engineer", r"\bsecurity (engineer|researcher)\b|\bappsec\b|\bpentest"),
    (
        "infra",
        "Infrastructure / SRE",
        r"\binfrastructure\b|\bplatform engineer\b|\bsite reliability\b|\bsre\b|\bdevops\b"
        r"|\bcloud engineer\b|\bdistributed systems\b|\bsystems engineer\b|\bkernel\b|\bcompilers?\b"
        r"|\bgpu\b|\bhpc\b|\bperformance engineer\b|\bproduction engineer\b",
    ),
    ("mobile", "Mobile engineer", r"\bios\b|\bandroid\b|\bmobile (engineer|developer)\b"),
    (
        "frontend",
        "Frontend engineer",
        r"\bfront[- ]?end\b|\bweb (engineer|developer)\b|\bui engineer\b",
    ),
    ("backend", "Backend engineer", r"\bback[- ]?end\b|\bserver[- ]side\b|\bapi engineer\b"),
    (
        "swe",
        "Software engineer",
        r"\bsoftware (engineer|developer|engineering)\b|\bswe\b|\bfull[- ]?stack\b|\bprogrammer\b"
        r"|\b(python|java|go|golang|rust|c\+\+|scala|kotlin|typescript) (engineer|developer)\b",
    ),
]
FAMILY_LABELS = {key: label for key, label, _ in FAMILIES}
FAMILY_LABELS["other_eng"] = "Other engineering"
_FAMILY_RE = [(key, re.compile(pattern, re.I)) for key, _, pattern in FAMILIES]
_ENGINEERING = re.compile(r"\b(engineer|engineering|developer|scientist|programmer)\b", re.I)

# Titles that are not engineering work even when they contain an
# engineering word ("Sales Engineer", "Engineering Recruiter").
_NOT_ENGINEERING = re.compile(
    r"\b(sales|account (executive|manager)|recruit(er|ing|ment)|talent|sourcer|marketing"
    r"|legal|counsel|paralegal|finance|financial analyst|accountant|accounting|payroll"
    r"|people partner|hr\b|human resources|office manager|executive assistant|customer success"
    r"|customer support|support specialist|business development|partnerships?|communications"
    r"|public policy|product designer|ux designer|ui designer|visual designer|brand|content"
    r"|copywriter|product manager|program manager|project manager|operations manager"
    r"|solutions? (engineer|architect|consultant)|pre-?sales|sales engineer)\b",
    re.I,
)

SENIORITY_LABELS = {
    "intern": "Intern / working student",
    "graduate": "Graduate / junior",
    "unstated": "Level not stated",
    "senior": "Senior",
    "staff": "Staff / principal / lead",
    "manager": "Manager / director",
}
_SENIORITY = [
    ("intern", r"\bintern(ship)?\b|\bplacement\b|\bworking student\b|\bwerkstudent\b|\btrainee\b"),
    (
        "graduate",
        r"\bgraduate\b|\bnew grad\b|\bentry[- ]level\b|\bjunior\b|\bjr\.?\b|\bearly[- ]career\b"
        r"|\bcampus\b|\b(engineer|developer) i\b",
    ),
    (
        "manager",
        r"\b(engineering |research )?manager\b|\bhead of\b|\bdirector\b|\bvp\b|\bvice president\b"
        r"|\bchief\b|\bcto\b",
    ),
    ("staff", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\blead\b|\barchitect\b|\bfellow\b"),
    ("senior", r"\bsenior\b|\bsr\.?\b|\b(engineer|developer) (iii|iv)\b"),
]
_SENIORITY_RE = [(key, re.compile(pattern, re.I)) for key, pattern in _SENIORITY]

DEFAULT_COUNTRIES = sorted(locations.EUROPE)
DEFAULT_PREFERENCES = """\
# eu-job-radar preferences. Edit freely; the inbox re-screens on every read.

[roles]
# Families to keep. All of them:
# sde, research_eng, ai, ml, data, quant, security, infra, mobile, frontend, backend, swe,
# other_eng (a title with "engineer"/"developer"/"scientist" that no family matched)
families = ["swe", "sde", "backend", "ml", "ai", "research_eng", "data", "infra", "quant"]
# Extra words that rule a title out, for example ["php", "salesforce"].
exclude_title_words = []

[seniority]
# Levels to keep: intern, graduate, unstated, senior, staff, manager
include = ["intern", "graduate", "unstated", "senior"]

[locations]
# ISO country codes (GB or UK for the United Kingdom). Default: EU, EEA,
# Switzerland and the UK.
countries = [{countries}]
# Keep remote postings open to Europe, EMEA or worldwide.
remote_europe = true
""".replace("{countries}", ", ".join(f'"{c}"' for c in DEFAULT_COUNTRIES))


class PreferencesError(ValueError):
    pass


@dataclass
class Preferences:
    families: set[str] = field(
        default_factory=lambda: (
            {"swe", "sde", "backend", "ml", "ai", "research_eng", "data"} | {"infra", "quant"}
        )
    )
    exclude_title_words: list[str] = field(default_factory=list)
    seniority: set[str] = field(
        default_factory=lambda: {"intern", "graduate", "unstated"} | {"senior"}
    )
    countries: set[str] = field(default_factory=lambda: set(DEFAULT_COUNTRIES))
    remote_europe: bool = True
    path: Path | None = None


def load_preferences(path: Path) -> Preferences:
    """Read preferences.toml. Unknown families, levels or countries are
    errors, so a typo never silently empties the inbox."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PreferencesError(f"{path} not found; run `job-radar init`") from None
    except tomllib.TOMLDecodeError as exc:
        raise PreferencesError(f"{path}: {exc}") from None
    prefs = Preferences(path=path)
    roles = data.get("roles", {})
    seniority = data.get("seniority", {})
    places = data.get("locations", {})
    errors = []
    if "families" in roles:
        prefs.families = set(_strings(roles["families"], "roles.families", errors))
        unknown = prefs.families - set(FAMILY_LABELS)
        if unknown:
            errors.append(
                f"roles.families: unknown {sorted(unknown)}; known {sorted(FAMILY_LABELS)}"
            )
    if "exclude_title_words" in roles:
        prefs.exclude_title_words = [
            w.lower()
            for w in _strings(roles["exclude_title_words"], "roles.exclude_title_words", errors)
        ]
    if "include" in seniority:
        prefs.seniority = set(_strings(seniority["include"], "seniority.include", errors))
        unknown = prefs.seniority - set(SENIORITY_LABELS)
        if unknown:
            errors.append(
                f"seniority.include: unknown {sorted(unknown)}; known {sorted(SENIORITY_LABELS)}"
            )
    if "countries" in places:
        codes = {
            locations.normalise_code(c)
            for c in _strings(places["countries"], "locations.countries", errors)
        }
        unknown = codes - set(locations.COUNTRY_NAMES)
        if unknown:
            errors.append(f"locations.countries: unknown codes {sorted(unknown)}")
        prefs.countries = codes
    if "remote_europe" in places:
        if not isinstance(places["remote_europe"], bool):
            errors.append("locations.remote_europe must be true or false")
        else:
            prefs.remote_europe = places["remote_europe"]
    if errors:
        raise PreferencesError(f"{path}: " + "; ".join(errors))
    return prefs


def _strings(value, name, errors) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        errors.append(f"{name} must be a list of strings")
        return []
    return value


# --- classification -------------------------------------------------------------------


def families(title: str) -> list[str]:
    found = [key for key, pattern in _FAMILY_RE if pattern.search(title)]
    if not found and _ENGINEERING.search(title):
        found = ["other_eng"]
    return found


def seniority(title: str) -> str:
    for key, pattern in _SENIORITY_RE:
        if pattern.search(title):
            return key
    return "unstated"


def not_engineering(title: str) -> str | None:
    match = _NOT_ENGINEERING.search(title)
    return match.group(0) if match else None


@dataclass
class Verdict:
    status: str  # relevant | check | outside
    families: list[str]
    seniority: str
    countries: list[str]
    remote: bool
    reasons: list[str]  # why it is outside or needs a check
    notes: list[str]  # what passed, for --details


def screen(posting: dict, prefs: Preferences) -> Verdict:
    """Screen one stored posting (a row as a dict with `title`, `locations`
    (list of strings) and optional `remote`)."""
    title = posting.get("title") or ""
    fams = families(title)
    level = seniority(title)
    place = locations.read_all(posting.get("locations") or [])
    if posting.get("remote"):
        place.remote = True
    outside, check, notes = [], [], []

    excluded = not_engineering(title)
    if excluded:
        outside.append(f"not an engineering role (title says '{excluded}')")
    for word in prefs.exclude_title_words:
        if re.search(rf"\b{re.escape(word)}\b", title, re.I):
            outside.append(f"title contains excluded word '{word}'")
    if not fams and not excluded:
        outside.append("no engineering role family recognised in the title")
    elif not excluded:
        wanted = [f for f in fams if f in prefs.families]
        if wanted:
            notes.append("role: " + ", ".join(FAMILY_LABELS[f] for f in wanted))
        else:
            outside.append(
                "role family not in your preferences ("
                + ", ".join(FAMILY_LABELS[f] for f in fams)
                + ")"
            )
    if level not in prefs.seniority:
        outside.append(f"level: {SENIORITY_LABELS[level]} is not in your preferences")
    else:
        notes.append(f"level: {SENIORITY_LABELS[level]}")

    matched = sorted(place.countries & prefs.countries)
    open_regions = place.regions & {"europe", "emea", "worldwide"}
    if matched:
        notes.append("location: " + ", ".join(locations.label(c) for c in matched))
    elif place.remote and open_regions and prefs.remote_europe:
        notes.append("location: remote, open to " + ", ".join(sorted(open_regions)))
    elif place.remote and not place.known:
        check.append("remote, but the posting does not say which countries it hires in")
    elif not place.known:
        shown = "; ".join(posting.get("locations") or []) or "none given"
        check.append(f"location not readable ({shown})")
    else:
        where = sorted(place.countries) or sorted(place.regions)
        outside.append("location outside your countries (" + ", ".join(where) + ")")

    status = "outside" if outside else "check" if check else "relevant"
    return Verdict(
        status, fams, level, sorted(place.countries), place.remote, outside + check, notes
    )

"""Reading free-text job locations into country codes.

Applicant tracking systems give locations as free text ("London, UK",
"Berlin / Remote", "Remote - EMEA", "Paris, Île-de-France, France"). This
module finds the countries a location names and whether it is remote, and
says when it cannot tell. It never guesses a country from a company's
headquarters: an unreadable location stays unknown.

Country codes are ISO 3166-1 alpha-2, with GB for the United Kingdom ("UK" is
accepted as an alias in preferences).
"""

import re
import unicodedata
from dataclasses import dataclass, field

# Europe as the screen understands it: EU, EEA, Switzerland and the UK.
EUROPE = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France",
    "DE": "Germany", "GR": "Greece", "HU": "Hungary", "IE": "Ireland", "IT": "Italy",
    "LV": "Latvia", "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia",
    "ES": "Spain", "SE": "Sweden", "IS": "Iceland", "LI": "Liechtenstein", "NO": "Norway",
    "CH": "Switzerland", "GB": "United Kingdom",
}  # fmt: skip

# Countries outside Europe that often appear in multi-location postings, so a
# "New York" or "Bangalore" posting is read as outside rather than unknown.
OTHER = {
    "US": "United States", "CA": "Canada", "MX": "Mexico", "BR": "Brazil", "AR": "Argentina",
    "IN": "India", "SG": "Singapore", "JP": "Japan", "KR": "South Korea", "CN": "China",
    "HK": "Hong Kong", "TW": "Taiwan", "AU": "Australia", "NZ": "New Zealand",
    "IL": "Israel", "AE": "United Arab Emirates", "TR": "Turkey", "ZA": "South Africa",
    "UA": "Ukraine", "RS": "Serbia", "EG": "Egypt", "NG": "Nigeria", "KE": "Kenya",
    "PH": "Philippines", "VN": "Vietnam", "ID": "Indonesia", "MY": "Malaysia", "TH": "Thailand",
    "CO": "Colombia", "CL": "Chile", "SA": "Saudi Arabia", "QA": "Qatar",
}  # fmt: skip

COUNTRY_NAMES = {**EUROPE, **OTHER}

_ALIASES = {
    "uk": "GB", "u.k.": "GB", "great britain": "GB", "britain": "GB", "england": "GB",
    "scotland": "GB", "wales": "GB", "northern ireland": "GB", "united kingdom": "GB",
    "usa": "US", "u.s.": "US", "u.s.a.": "US", "united states of america": "US",
    "deutschland": "DE", "nederland": "NL", "the netherlands": "NL", "holland": "NL",
    "espana": "ES", "españa": "ES", "sverige": "SE", "danmark": "DK", "suomi": "FI",
    "norge": "NO", "osterreich": "AT", "österreich": "AT", "schweiz": "CH", "suisse": "CH",
    "svizzera": "CH", "polska": "PL", "czech republic": "CZ", "italia": "IT", "eire": "IE",
    "republic of ireland": "IE", "uae": "AE", "korea": "KR", "republic of korea": "KR",
    "gbr": "GB", "can": "CA", "deu": "DE", "fra": "FR", "nld": "NL",
    "irl": "IE", "che": "CH", "swe": "SE",
    "ontario": "CA", "quebec": "CA", "british columbia": "CA", "alberta": "CA",
}  # fmt: skip

# Cities that identify a country on their own. A few names are shared with
# North American cities (see _AMBIGUOUS_CITIES); those count as European only
# when the same text names no North American place.
_CITIES = {
    "GB": "london cambridge oxford edinburgh glasgow manchester bristol leeds birmingham "
    "belfast cardiff reading sheffield newcastle nottingham bath brighton guildford "
    "hatfield milton keynes york southampton",
    "IE": "dublin cork galway limerick",
    "DE": "berlin munich münchen munchen hamburg frankfurt cologne köln koln stuttgart "
    "dusseldorf düsseldorf leipzig dresden heidelberg tübingen tubingen karlsruhe "
    "walldorf freiburg hannover nuremberg nürnberg bonn aachen darmstadt potsdam",
    "NL": "amsterdam rotterdam utrecht eindhoven the hague den haag delft leiden "
    "groningen veldhoven",
    "FR": "paris lyon grenoble toulouse marseille lille nantes bordeaux nice "
    "sophia antipolis rennes montpellier strasbourg",
    "CH": "zurich zürich geneva genève genf lausanne basel bern zug lugano",
    "SE": "stockholm gothenburg göteborg goteborg malmö malmo lund uppsala linköping",
    "DK": "copenhagen københavn aarhus odense",
    "FI": "helsinki espoo tampere oulu turku",
    "NO": "oslo bergen trondheim",
    "ES": "barcelona madrid valencia málaga malaga seville sevilla bilbao",
    "PT": "lisbon lisboa porto braga",
    "IT": "milan milano rome roma turin torino bologna",
    "PL": "warsaw warszawa kraków krakow cracow wrocław wroclaw gdańsk gdansk poznań "
    "poznan łódź lodz katowice",
    "AT": "vienna wien graz linz",
    "BE": "brussels bruxelles antwerp ghent leuven",
    "CZ": "prague praha brno",
    "EE": "tallinn tartu",
    "LU": "luxembourg city",
    "HU": "budapest",
    "RO": "bucharest cluj-napoca cluj iasi",
    "GR": "athens thessaloniki",
    "LT": "vilnius kaunas",
    "LV": "riga",
    "BG": "sofia",
    "HR": "zagreb",
    "SK": "bratislava",
    "SI": "ljubljana",
    "CY": "limassol nicosia",
    "MT": "valletta",
    "US": "new york nyc san francisco seattle boston austin chicago los angeles "
    "mountain view palo alto menlo park sunnyvale redmond denver atlanta washington dc "
    "bay area san jose san diego pittsburgh detroit bellevue",
    "CA": "toronto montreal montréal vancouver waterloo ottawa calgary",
    "IN": "bangalore bengaluru hyderabad pune mumbai delhi gurgaon gurugram noida chennai",
    "SG": "singapore",
    "JP": "tokyo osaka",
    "IL": "tel aviv haifa jerusalem",
    "AU": "sydney melbourne brisbane",
    "AE": "dubai abu dhabi",
    "CN": "beijing shanghai shenzhen",
    "HK": "hong kong",
    "KR": "seoul",
    "BR": "são paulo sao paulo",
}
_US_STATES = (
    "alabama alaska arizona arkansas california colorado connecticut delaware florida "
    "georgia hawaii idaho illinois indiana iowa kansas kentucky louisiana maine maryland "
    "massachusetts michigan minnesota mississippi missouri montana nebraska nevada "
    "ohio oklahoma oregon pennsylvania tennessee texas utah vermont virginia wisconsin "
    "wyoming washington"
).split()
_US_STATE_CODES = set(
    "AL AK AZ AR CA CO CT DE FL HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)

REGION_WORDS = {
    "europe": "europe", "european union": "europe", "eu": "europe", "eea": "europe",
    "emea": "emea", "dach": "europe", "nordics": "europe", "benelux": "europe",
    "americas": "americas", "amer": "americas", "north america": "americas", "latam": "americas",
    "apac": "apac", "asia": "apac", "worldwide": "worldwide", "anywhere": "worldwide",
    "global": "worldwide",
}  # fmt: skip
REGION_COUNTRIES = {"dach": {"DE", "AT", "CH"}, "benelux": {"BE", "NL", "LU"},
                    "nordics": {"SE", "DK", "FI", "NO", "IS"}}  # fmt: skip

_REMOTE = re.compile(r"\b(remote|work from home|wfh|home[- ]based|distributed|anywhere)\b", re.I)
_HYBRID = re.compile(r"\bhybrid\b", re.I)


def _fold(text: str) -> str:
    return unicodedata.normalize("NFC", text).casefold()


def _build_phrases() -> list[tuple[str, str]]:
    phrases: dict[str, str] = {}
    for code, name in COUNTRY_NAMES.items():
        phrases[_fold(name)] = code
    phrases.update(_ALIASES)
    for code, cities in _CITIES.items():
        # Multi-word cities are listed with spaces; split on known single names.
        for city in _split_cities(cities):
            phrases.setdefault(city, code)
    for state in _US_STATES:
        phrases.setdefault(state, "US")
    # Longest phrases first so "new york" wins over "york".
    return sorted(phrases.items(), key=lambda item: -len(item[0]))


_MULTI = {
    "milton keynes", "the hague", "den haag", "sophia antipolis", "luxembourg city",
    "new york", "san francisco", "los angeles", "mountain view", "palo alto", "menlo park",
    "washington dc", "bay area", "san jose", "san diego", "tel aviv", "abu dhabi",
    "hong kong", "são paulo", "sao paulo",
}  # fmt: skip


def _split_cities(text: str) -> list[str]:
    out, rest = [], text
    for multi in sorted(_MULTI, key=len, reverse=True):
        if multi in rest:
            out.append(multi)
            rest = rest.replace(multi, " ")
    return out + rest.split()


_PHRASES = _build_phrases()
_CITY_NAMES = {_fold(city) for cities in _CITIES.values() for city in _split_cities(cities)}
_AMBIGUOUS_CITIES = {
    "cambridge", "birmingham", "paris", "london", "reading", "bath", "york", "manchester",
    "dublin", "athens", "valencia", "toledo", "berlin", "hamburg", "vienna", "florence",
}  # fmt: skip


@dataclass
class Place:
    """What a location text says. `countries` are only those it names."""

    countries: set[str] = field(default_factory=set)
    remote: bool = False
    hybrid: bool = False
    regions: set[str] = field(default_factory=set)  # europe, emea, americas, apac, worldwide
    unread: list[str] = field(default_factory=list)  # parts that named nothing we know

    @property
    def known(self) -> bool:
        return bool(self.countries or self.regions)

    def merge(self, other: "Place") -> "Place":
        return Place(
            self.countries | other.countries,
            self.remote or other.remote,
            self.hybrid or other.hybrid,
            self.regions | other.regions,
            self.unread + other.unread,
        )


def _find(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(phrase)}(?![\w])", text) is not None


_SEPARATORS = re.compile(r"\s*(?:;|\||•|\n|/|\bor\b)\s*", re.I)


def read(text: str | None) -> Place:
    """Read one location string. A list of places ("San Francisco, CA; London")
    is read part by part, so one part's country cannot hide another's."""
    if not text or not text.strip():
        return Place()
    parts = [part for part in _SEPARATORS.split(text) if part and part.strip()]
    if len(parts) > 1:
        return read_all(parts)
    # Separate comma-delimited city lists before resolving US/Canadian
    # qualifiers, so "New York, NY, London" keeps both countries.
    comma_parts = text.split(",")
    groups = []
    current = comma_parts[0]
    for part in comma_parts[1:]:
        if _fold(part.strip()) in _CITY_NAMES and _fold(part.strip()) not in {
            "singapore",
            "hong kong",
        }:
            groups.append(current)
            current = part
        else:
            current += "," + part
    if groups:
        return read_all(groups + [current])
    return _read_one(text)


def _read_one(text: str) -> Place:
    place = Place()
    folded = _fold(text)
    place.remote = bool(_REMOTE.search(text))
    place.hybrid = bool(_HYBRID.search(text))
    remaining = folded
    ambiguous: set[str] = set()
    for phrase, code in _PHRASES:
        if _find(remaining, phrase):
            (ambiguous if phrase in _AMBIGUOUS_CITIES else place.countries).add(code)
            remaining = re.sub(rf"(?<![\w]){re.escape(phrase)}(?![\w])", " ", remaining)
    for word, region in REGION_WORDS.items():
        if _find(remaining, word):
            place.regions.add(region)
            place.countries |= REGION_COUNTRIES.get(word, set())
    if re.search(r",\s*(ON|QC|BC|AB|NS|NB|PE|MB)(?:\s*(?:,|$))", text):
        place.countries.add("CA")
    # Two-letter codes, only in upper case in the original text ("London, GB",
    # "Austin, TX"), so ordinary words are never read as codes.
    for token in re.findall(r"\b[A-Z]{2}\b", text):
        if token == "UK":
            place.countries.add("GB")
        elif token in COUNTRY_NAMES and text.strip() == token:
            place.countries.add(token)
        elif token in _US_STATE_CODES and "US" in place.countries:
            continue  # e.g. DE means Delaware in an explicitly US address
        elif token == "CA" and "CA" in place.countries:
            continue
        elif (
            token in {"ON", "QC", "BC", "AB", "NS", "NB", "NL", "PE", "SK", "MB"}
            and "CA" in place.countries
        ):
            continue
        elif token in _US_STATE_CODES and token not in EUROPE:
            place.countries.add("US")
        elif token in COUNTRY_NAMES and token not in {"IN", "IT"}:
            # "IN" and "IT" are too often ordinary words in titles.
            place.countries.add(token)
    # A city name shared with North America ("Cambridge, MA", "London, ON")
    # counts only when nothing in the text names a North American place.
    if not place.countries & {"US", "CA"} and not (
        _find(folded, "cambridge") and not place.countries
    ):
        place.countries |= ambiguous
    if not place.known:
        leftover = re.sub(r"[\W\d_]+", " ", remaining).strip()
        if leftover and not place.remote:
            place.unread.append(text.strip())
    return place


def read_all(texts) -> Place:
    place = Place()
    for text in texts:
        place = place.merge(read(text))
    return place


def normalise_code(value: str) -> str:
    code = value.strip().upper()
    return "GB" if code == "UK" else code


def label(code: str) -> str:
    return COUNTRY_NAMES.get(code, code)

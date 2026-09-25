"""robots.txt parsing and matching (RFC 9309).

A robots file is one piece of access evidence, not permission: collection
also needs a reviewed access policy in the source registry. This module only
answers whether the file's rules allow a path for our product token.

* Groups naming our product token apply; otherwise the `*` groups apply.
* Rules match the path and query. `*` matches any characters and a trailing
  `$` anchors the end. The longest matching rule wins; on a tie, Allow wins.
* Crawl-delay is not in the RFC, but where a site states one for our group
  it is honoured as a stricter rate limit.
"""

import math
import re
from dataclasses import dataclass, field
from urllib.parse import quote, urlsplit

PRODUCT_TOKEN = "eu-job-radar"


@dataclass
class Rule:
    allow: bool
    pattern: str

    def __post_init__(self):
        self.pattern = _normalise(self.pattern)
        body = self.pattern
        anchored = body.endswith("$")
        if anchored:
            body = body[:-1]
        regex = ".*".join(re.escape(part) for part in body.split("*"))
        self._regex = re.compile(regex + ("$" if anchored else ""), re.DOTALL)

    def matches(self, path: str) -> bool:
        return bool(self._regex.match(path))


@dataclass
class Group:
    agents: list[str] = field(default_factory=list)
    rules: list[Rule] = field(default_factory=list)
    crawl_delay: float | None = None


@dataclass
class RobotsPolicy:
    """The rules that apply to one product token."""

    rules: list[Rule]
    crawl_delay: float | None
    matched_group: str  # "product", "*" or "none"
    sitemaps: list[str] = field(default_factory=list)

    def allows(self, url_or_path: str) -> bool:
        return self.decision(url_or_path)[0]

    def decision(self, url_or_path: str) -> tuple[bool, str | None]:
        """(allowed, the deciding rule as text or None when no rule matched)."""
        path = _path_of(url_or_path)
        if path == "/robots.txt":
            return True, None
        best: Rule | None = None
        for rule in self.rules:
            if not rule.pattern or not rule.matches(path):
                continue
            if (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
            ):
                best = rule
        if best is None:
            return True, None
        return best.allow, f"{'Allow' if best.allow else 'Disallow'}: {best.pattern}"


_PERCENT = re.compile(r"%[0-9a-fA-F]{2}")


def _normalise(text: str) -> str:
    """Percent-encode non-ASCII characters and upper-case existing escapes, so
    `Disallow: /jobs/ö` and a request for `/jobs/%c3%b6` compare equal."""
    encoded = quote(text, safe="/?=&*$:@!,;+%-._~()'[]")
    return _PERCENT.sub(lambda m: m.group(0).upper(), encoded)


def _path_of(url_or_path: str) -> str:
    if url_or_path.startswith("/"):
        return _normalise(url_or_path)
    parts = urlsplit(url_or_path)
    path = parts.path or "/"
    return _normalise(path + (f"?{parts.query}" if parts.query else ""))


def parse(text: str, product_token: str = PRODUCT_TOKEN) -> RobotsPolicy:
    groups: list[Group] = []
    sitemaps: list[str] = []
    current: Group | None = None
    last_was_agent = False
    text = text.lstrip("\ufeff")  # a UTF-8 byte-order mark must not hide the first group
    for raw_line in text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if current is None or not last_was_agent:
                current = Group()
                groups.append(current)
            current.agents.append(value.lower())
            last_was_agent = True
            continue
        last_was_agent = False
        if key == "sitemap":
            sitemaps.append(value)
            continue
        if current is None:
            continue
        if key in {"allow", "disallow"}:
            # An empty Disallow allows everything; it adds no rule.
            if value:
                current.rules.append(Rule(key == "allow", value))
        elif key == "crawl-delay":
            try:
                delay = float(value)
            except ValueError:
                continue
            if math.isfinite(delay) and delay >= 0:
                current.crawl_delay = delay
    token = product_token.lower()
    chosen = [g for g in groups if token in g.agents]
    matched = "product"
    if not chosen:
        chosen = [g for g in groups if "*" in g.agents]
        matched = "*" if chosen else "none"
    rules = [rule for g in chosen for rule in g.rules]
    delays = [g.crawl_delay for g in chosen if g.crawl_delay is not None]
    return RobotsPolicy(rules, max(delays) if delays else None, matched, sitemaps)


def allow_all(reason: str) -> RobotsPolicy:
    """No robots rules apply (for example the file returned 404). Recorded as
    evidence; it is not permission to collect."""
    return RobotsPolicy([], None, f"none ({reason})")


def disallow_all(reason: str) -> RobotsPolicy:
    """The file could not be read (server error or unreachable), so RFC 9309
    says to assume everything is disallowed."""
    return RobotsPolicy([Rule(False, "/")], None, f"assumed disallow ({reason})")

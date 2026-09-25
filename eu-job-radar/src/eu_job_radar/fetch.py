"""The shared, policy-aware HTTP layer every live adapter uses.

Adapters never open connections themselves. `HttpClient.for_source` gives an
adapter a `SourceFetcher` that enforces, for every request:

* Network safety: only http(s) on ports 80/443, no credentials in URLs, no
  IP-literal hosts, and every resolved address must be public (no loopback,
  private, link-local, carrier-grade NAT, multicast, reserved or cloud
  metadata addresses). The connection is pinned to the checked address, so
  DNS cannot be switched between the check and the request. Redirects are
  followed by hand, re-checked, limited in number and kept to the source's
  registered hosts. No cookies or credentials are ever sent.
* Access policy: robots.txt is read (through this same layer) before a host
  is used; a disallowed path is refused without requesting it. A missing
  robots file is recorded, but it is not permission; that comes from the
  reviewed registry entry.
* Politeness: at most one request per second per host (or per shared rate
  group), slower when the registry or a Crawl-delay says so; an identifiable
  user-agent; bounded retries with backoff; Retry-After honoured up to a cap.
* Budgets: a run-wide and a per-source request budget; exhausting either
  raises BudgetExhausted so the adapter can report a partial result.
* Caching: ETag / Last-Modified conditional requests with a local cache; a
  304 reuses the cached body. Cache files stay in the workspace, outside Git,
  and are pruned after the source's retention period.
* Validation: timeouts, a response-size limit (also after gzip decoding),
  expected content types (HTML where a feed was expected is an error, not an
  empty feed) and character decoding.

Every attempt is logged as a FetchRecord for run accounting. Headers other
than validators and content type are not logged, and nothing sensitive is
sent, so there is nothing to redact beyond the URL itself.
"""

import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Protocol
from urllib.parse import urljoin, urlsplit

from . import __version__, robots
from .clock import iso

PROJECT_URL = "https://github.com/Abdulla4akash/eu-job-radar"
ALLOWED_PORTS = {"http": 80, "https": 443}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
RETRY_STATUSES = {429, 500, 502, 503, 504}
CONTENT_TYPES = {
    "xml": ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml"),
    "json": ("application/json",),
    "html": ("text/html", "application/xhtml+xml"),
    "text": ("text/plain",),
}
_EXTRA_BLOCKED_NETWORKS = [
    ipaddress.ip_network("100.64.0.0/10"),  # carrier-grade NAT
    ipaddress.ip_network("169.254.0.0/16"),  # link-local, cloud metadata
    ipaddress.ip_network("fd00:ec2::/32"),  # AWS IPv6 metadata
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
]


def user_agent(contact: str | None = None) -> str:
    """An identifiable user-agent. The contact is the user's own optional
    setting; without it the project URL identifies the tool."""
    text = f"eu-job-radar/{__version__} (+{PROJECT_URL}; personal job search tool; low-rate"
    text += " manual collection"
    if contact:
        text += f"; contact: {contact}"
    return text + ")"


# --- errors ---------------------------------------------------------------------


class FetchError(Exception):
    kind = "fetch_error"

    def __init__(self, message: str, *, url: str | None = None, status: int | None = None):
        super().__init__(message)
        self.url = url
        self.status = status


class UnsafeURL(FetchError):
    kind = "unsafe_url"


class BlockedByRobots(FetchError):
    kind = "blocked_by_robots"


class HostNotAllowed(FetchError):
    kind = "host_not_registered"


class BudgetExhausted(FetchError):
    kind = "budget_exhausted"


class AccessDenied(FetchError):
    """403: access refused. Never retried or worked around."""

    kind = "access_denied"


class AuthRequired(FetchError):
    kind = "authentication_required"


class NotFound(FetchError):
    kind = "not_found"


class RateLimited(FetchError):
    kind = "rate_limited"


class ServerError(FetchError):
    kind = "server_error"


class TransportFailure(FetchError):
    kind = "transport_failure"


class ResponseTooLarge(FetchError):
    kind = "response_too_large"


class UnexpectedContent(FetchError):
    kind = "unexpected_content"


class RedirectError(FetchError):
    kind = "redirect_error"


class RequestError(FetchError):
    """The request itself was invalid (not a network failure); not retried."""

    kind = "request_error"


MAX_CRAWL_DELAY = 60.0


# --- time, budgets and rate limits -------------------------------------------------


class Timer(Protocol):
    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...


class RealTimer:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)


@dataclass
class Budget:
    """A request budget. `limit` None means unlimited (never used for runs)."""

    limit: int | None
    label: str
    used: int = 0

    @property
    def remaining(self) -> int | None:
        return None if self.limit is None else max(self.limit - self.used, 0)

    def check(self) -> None:
        if self.limit is not None and self.used >= self.limit:
            raise BudgetExhausted(f"{self.label} request budget of {self.limit} reached")

    def spend(self) -> None:
        self.used += 1


class RateLimiter:
    """Minimum spacing between requests per rate key (a host, or a group of
    hosts on one platform)."""

    def __init__(self, timer: Timer):
        self.timer = timer
        self.last: dict[str, float] = {}
        self.waited: float = 0.0

    def wait(self, key: str, interval: float) -> float:
        now = self.timer.monotonic()
        previous = self.last.get(key)
        delay = 0.0
        if previous is not None:
            delay = previous + interval - now
            if delay > 0:
                self.timer.sleep(delay)
                self.waited += delay
            else:
                delay = 0.0
        self.last[key] = self.timer.monotonic()
        return delay


# --- network safety -------------------------------------------------------------


def _blocked_address(address: str) -> str | None:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    for attribute in (
        "is_private",
        "is_loopback",
        "is_link_local",
        "is_multicast",
        "is_reserved",
        "is_unspecified",
    ):
        if getattr(ip, attribute):
            return attribute.removeprefix("is_").replace("_", "-")
    if any(ip in network for network in _EXTRA_BLOCKED_NETWORKS if network.version == ip.version):
        return "non-public"
    if not ip.is_global:
        return "non-global"
    return None


def check_url(url: str) -> tuple[str, str, int]:
    """Validate the form of a URL before any lookup. Returns (scheme, host, port)."""
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURL("URL missing or too long", url=str(url)[:200])
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise UnsafeURL("URL contains whitespace or control characters", url=url[:200])
    if not url.isascii():
        raise UnsafeURL("URL contains non-ASCII characters; it must be percent-encoded", url=url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ALLOWED_PORTS:
        raise UnsafeURL(f"only http(s) URLs are fetched, not {scheme or 'no scheme'!r}", url=url)
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise UnsafeURL("URLs with credentials are refused", url=url)
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURL("URL has no host", url=url)
    try:
        port = parts.port or ALLOWED_PORTS[scheme]
    except ValueError as exc:
        raise UnsafeURL(f"invalid port: {exc}", url=url) from None
    if port != ALLOWED_PORTS[scheme]:
        raise UnsafeURL(f"port {port} refused; only 80 (http) and 443 (https)", url=url)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UnsafeURL("IP-literal hosts are refused; registered sources use names", url=url)
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".arpa")):
        raise UnsafeURL(f"host {host!r} is not a public name", url=url)
    if "." not in host:
        raise UnsafeURL(f"host {host!r} is not a fully qualified public name", url=url)
    return scheme, host, port


def default_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(info[4][0] for info in infos))


def safe_address(host: str, port: int, resolver) -> str:
    """Resolve a host and require every address to be public. Returns the
    address to connect to."""
    try:
        addresses = resolver(host, port)
    except OSError as exc:
        raise TransportFailure(f"cannot resolve {host}: {exc}") from None
    if not addresses:
        raise TransportFailure(f"{host} did not resolve")
    for address in addresses:
        reason = _blocked_address(address)
        if reason:
            raise UnsafeURL(f"{host} resolves to a {reason} address ({address}); refused")
    return addresses[0]


# --- transport --------------------------------------------------------------------


@dataclass
class HttpRequest:
    url: str
    host: str
    port: int
    scheme: str
    address: str
    headers: dict[str, str]
    connect_timeout: float
    read_timeout: float
    max_bytes: int


@dataclass
class RawResponse:
    status: int
    headers: dict[str, str]  # lower-case names
    body: bytes
    reason: str = ""


class Transport(Protocol):
    def send(self, request: HttpRequest) -> RawResponse: ...


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, request: HttpRequest, context: ssl.SSLContext):
        super().__init__(request.host, request.port, timeout=request.read_timeout, context=context)
        self._pinned = request.address
        self._connect_timeout = request.connect_timeout

    def connect(self):
        sock = socket.create_connection((self._pinned, self.port), self._connect_timeout)
        sock.settimeout(self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, request: HttpRequest):
        super().__init__(request.host, request.port, timeout=request.read_timeout)
        self._pinned = request.address
        self._connect_timeout = request.connect_timeout

    def connect(self):
        self.sock = socket.create_connection((self._pinned, self.port), self._connect_timeout)
        self.sock.settimeout(self.timeout)


class PinnedTransport:
    """Real network transport: connects to the pre-checked address, verifies
    TLS against the host name, reads at most max_bytes and never follows
    redirects itself. Proxies from the environment are not used."""

    def __init__(self):
        self.context = ssl.create_default_context()

    def send(self, request: HttpRequest) -> RawResponse:
        if request.scheme == "https":
            conn = _PinnedHTTPSConnection(request, self.context)
        else:
            conn = _PinnedHTTPConnection(request)
        parts = urlsplit(request.url)
        target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        started = time.monotonic()
        try:
            conn.request("GET", target, headers=request.headers)
            response = conn.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            declared = headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > request.max_bytes:
                raise ResponseTooLarge(
                    f"declared size {declared} bytes exceeds the {request.max_bytes}-byte limit",
                    url=request.url,
                )
            chunks, size = [], 0
            while True:
                chunk = response.read(65536)
                if not chunk:
                    break
                size += len(chunk)
                if size > request.max_bytes:
                    raise ResponseTooLarge(
                        f"response exceeds the {request.max_bytes}-byte limit", url=request.url
                    )
                if time.monotonic() - started > request.read_timeout * 3:
                    raise TransportFailure("response took too long to arrive", url=request.url)
                chunks.append(chunk)
            return RawResponse(response.status, headers, b"".join(chunks), response.reason)
        except TimeoutError as exc:
            raise TransportFailure(f"timed out: {exc}", url=request.url) from None
        except (UnicodeError, ValueError) as exc:
            # For example a header that is not encodable: never retried.
            raise RequestError(f"request could not be sent: {exc}", url=request.url) from None
        except (OSError, http.client.HTTPException) as exc:
            raise TransportFailure(f"{type(exc).__name__}: {exc}", url=request.url) from None
        finally:
            conn.close()


# --- cache ------------------------------------------------------------------------


class ResponseCache:
    """Validators and bodies for conditional requests, in the workspace.
    Bodies are local copies kept only for the source's retention period."""

    def __init__(self, directory: Path | None):
        self.directory = directory

    def _paths(self, url: str) -> tuple[Path, Path] | None:
        if self.directory is None:
            return None
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / f"{key}.json", self.directory / f"{key}.body"

    def get(self, url: str) -> dict | None:
        paths = self._paths(url)
        if not paths or not paths[0].is_file() or not paths[1].is_file():
            return None
        try:
            meta = json.loads(paths[0].read_text("utf-8"))
            meta["body"] = paths[1].read_bytes()
        except (OSError, ValueError):
            return None
        return meta if meta.get("url") == url else None

    def put(self, url: str, meta: dict, body: bytes) -> None:
        paths = self._paths(url)
        if not paths:
            return
        self.directory.mkdir(parents=True, exist_ok=True)
        paths[1].write_bytes(body)
        paths[0].write_text(json.dumps({**meta, "url": url}), "utf-8")

    def prune(
        self, now: datetime, max_age_days: float, per_source: dict[str, float] | None = None
    ) -> int:
        """Delete entries older than their source's retention (else
        max_age_days). Returns how many were removed."""
        if self.directory is None or not self.directory.is_dir():
            return 0
        removed = 0
        for meta_path in self.directory.glob("*.json"):
            days = max_age_days
            try:
                meta = json.loads(meta_path.read_text("utf-8"))
                stored = datetime.fromisoformat(meta["stored_at"].replace("Z", "+00:00"))
                days = (per_source or {}).get(meta.get("source_id"), max_age_days)
            except (OSError, ValueError, KeyError):
                stored = datetime.min.replace(tzinfo=UTC)
            if stored < now - timedelta(days=days):
                meta_path.unlink(missing_ok=True)
                meta_path.with_suffix(".body").unlink(missing_ok=True)
                removed += 1
        return removed


# --- client -------------------------------------------------------------------------


@dataclass
class FetchPolicy:
    user_agent: str
    min_interval: float = 1.0
    connect_timeout: float = 10.0
    read_timeout: float = 20.0
    max_bytes: int = 5_000_000
    max_retries: int = 2
    backoff_seconds: float = 2.0
    max_retry_after: float = 60.0
    max_redirects: int = 3
    # A plain language preference, so sites answer in English where they can.
    accept_language: str = "en;q=1.0, *;q=0.5"


@dataclass
class Response:
    url: str  # the requested URL
    final_url: str  # after redirects
    status: int
    headers: dict[str, str]
    body: bytes
    text: str
    from_cache: bool = False  # a 304 reused the cached body
    encoding: str = "utf-8"


@dataclass
class FetchRecord:
    source_id: str | None
    at: str
    url: str
    purpose: str
    status: int | None
    result: str  # ok | not_modified | error | refused
    bytes: int | None
    elapsed_ms: int | None
    attempt: int
    detail: str | None = None


def _retry_after(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - now).total_seconds(), 0.0)


def _media_type(headers: dict[str, str]) -> tuple[str, str | None]:
    value = headers.get("content-type", "")
    media = value.split(";", 1)[0].strip().lower()
    match = re.search(r"charset=[\"']?([\w.:-]+)", value, re.I)
    return media, match.group(1).lower() if match else None


_HTML_START = re.compile(rb"^\s*(<!doctype\s+html|<html)", re.I)
_META_CHARSET = re.compile(rb"<meta[^>]+charset=[\"']?([\w.:-]+)", re.I)
_XML_ENCODING = re.compile(rb"^<\?xml[^>]*encoding=[\"']([\w.:-]+)", re.I)


def decode_body(body: bytes, charset: str | None, expect: str) -> tuple[str, str]:
    """Decode with the declared charset, then an in-document declaration, then
    UTF-8. Undecodable bytes are replaced rather than guessed."""
    candidates = [charset]
    if expect == "html":
        match = _META_CHARSET.search(body[:4096])
        candidates.append(match.group(1).decode("ascii") if match else None)
    if expect == "xml":
        match = _XML_ENCODING.search(body[:200])
        candidates.append(match.group(1).decode("ascii") if match else None)
    candidates.append("utf-8")
    for name in candidates:
        if not name:
            continue
        try:
            return body.decode(name), name
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace"), "utf-8 (with replacements)"


def _gunzip(body: bytes, limit: int, url: str) -> bytes:
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = decoder.decompress(body, limit + 1)
    except zlib.error as exc:
        raise UnexpectedContent(f"corrupt gzip response: {exc}", url=url) from None
    if len(out) > limit or decoder.unconsumed_tail:
        raise ResponseTooLarge(f"decompressed response exceeds the {limit}-byte limit", url=url)
    if not decoder.eof:
        raise UnexpectedContent("truncated gzip response (stream did not end)", url=url)
    return out


class HttpClient:
    """Shared by all sources in a run: one rate limiter, one run budget, one
    robots cache, one response cache and one log."""

    def __init__(
        self,
        *,
        policy: FetchPolicy,
        transport: Transport | None = None,
        resolver: Callable[[str, int], list[str]] | None = None,
        timer: Timer | None = None,
        clock=None,
        cache: ResponseCache | None = None,
        run_budget: Budget | None = None,
    ):
        from .clock import SystemClock

        self.policy = policy
        self.transport = transport or PinnedTransport()
        self.resolver = resolver or (lambda host, port: default_resolver(host, port))
        self.timer = timer or RealTimer()
        self.clock = clock or SystemClock()
        self.cache = cache or ResponseCache(None)
        self.run_budget = run_budget or Budget(None, "run")
        self.limiter = RateLimiter(self.timer)
        self.log: list[FetchRecord] = []
        self._robots: dict[str, robots.RobotsPolicy] = {}

    def for_source(
        self,
        source_id: str,
        *,
        hosts: list[str],
        rate_seconds: float | None = None,
        rate_group: str | None = None,
        max_requests: int | None = None,
        cache_retention_days: float = 7.0,
        respect_robots: bool = True,
    ) -> "SourceFetcher":
        return SourceFetcher(
            self,
            source_id,
            hosts=[h.lower() for h in hosts],
            interval=max(self.policy.min_interval, rate_seconds or 0.0),
            rate_group=rate_group,
            budget=Budget(max_requests, f"{source_id} source"),
            cache_retention_days=cache_retention_days,
            respect_robots=respect_robots,
        )


class SourceFetcher:
    def __init__(
        self,
        client: HttpClient,
        source_id: str,
        *,
        hosts: list[str],
        interval: float,
        rate_group: str | None,
        budget: Budget,
        cache_retention_days: float,
        respect_robots: bool,
    ):
        self.client = client
        self.source_id = source_id
        self.hosts = hosts
        self.interval = interval
        self.rate_group = rate_group
        self.budget = budget
        self.cache_retention_days = cache_retention_days
        self.respect_robots = respect_robots
        self.requests = 0
        self.not_modified = 0
        self.robots_evidence: dict[str, str] = {}

    # Accounting helpers ------------------------------------------------------------

    @property
    def records(self) -> list[FetchRecord]:
        return [r for r in self.client.log if r.source_id == self.source_id]

    def _log(self, url, purpose, status, result, size, elapsed, attempt, detail=None):
        self.client.log.append(
            FetchRecord(
                self.source_id,
                iso(self.client.clock.now()),
                url,
                purpose,
                status,
                result,
                size,
                elapsed,
                attempt,
                detail[:300] if detail else None,
            )
        )

    # Public API ----------------------------------------------------------------------

    def mark_detail_checked(self, url: str, *, unavailable: bool = False) -> None:
        """Annotate the original request only after its adapter validated
        the record. Keep status, redirect evidence and request counts intact."""
        for record in reversed(self.records):
            if record.url == url and record.purpose == "detail":
                if record.result in {"ok", "not_modified", "redirect"}:
                    record.result = "detail_unavailable" if unavailable else "detail_parsed"
                return

    def get(
        self,
        url: str,
        *,
        expect: str,
        purpose: str = "listing",
        conditional: bool = True,
        follow: Callable[[str], bool] | None = None,
    ) -> Response:
        """Fetch one resource, following safe redirects. Raises a FetchError
        subclass on any refusal or failure; never returns an error page as data.
        `follow`, when given, decides which redirect targets may be followed;
        a refused redirect raises RedirectError without requesting the target
        (for example a withdrawn advert redirecting to a search page)."""
        self._check_host(url)
        if self.respect_robots:
            self._check_robots(url)
        current = url
        for _hop in range(self.client.policy.max_redirects + 1):
            response = self._request(
                current, expect=expect, purpose=purpose, conditional=conditional
            )
            if isinstance(response, Response):
                return response
            location = response  # a validated redirect target
            if follow is not None and not follow(location):
                raise RedirectError(f"redirected to {location}; not followed", url=current)
            if urlsplit(current).scheme == "https" and urlsplit(location).scheme != "https":
                raise RedirectError("redirect from https to http refused", url=current)
            self._check_host(location)
            if self.respect_robots:
                self._check_robots(location)
            current = location
        raise RedirectError(f"more than {self.client.policy.max_redirects} redirects", url=url)

    # Internals -----------------------------------------------------------------------

    def _check_host(self, url: str) -> None:
        _, host, _ = check_url(url)
        if host not in self.hosts:
            raise HostNotAllowed(
                f"{host} is not a registered host for {self.source_id} ({', '.join(self.hosts)})",
                url=url,
            )

    def _check_robots(self, url: str) -> None:
        scheme, host, _ = check_url(url)
        policy = self.client._robots.get(host)
        if policy is None:
            policy = self._load_robots(scheme, host)
            self.client._robots[host] = policy
        allowed, rule = policy.decision(url)
        if not allowed:
            self._log(url, "robots-check", None, "refused", None, None, 0, f"robots.txt {rule}")
            raise BlockedByRobots(f"robots.txt disallows this path ({rule})", url=url)
        if policy.crawl_delay:
            if policy.crawl_delay > MAX_CRAWL_DELAY:
                self._log(
                    url,
                    "robots-check",
                    None,
                    "refused",
                    None,
                    None,
                    0,
                    f"Crawl-delay {policy.crawl_delay}",
                )
                raise BlockedByRobots(
                    f"robots.txt asks for a Crawl-delay of {policy.crawl_delay:.0f}s, more than "
                    f"the {MAX_CRAWL_DELAY:.0f}s this tool will wait; source refused",
                    url=url,
                )
            self.interval = max(self.interval, policy.crawl_delay)

    def _load_robots(self, scheme: str, host: str) -> robots.RobotsPolicy:
        robots_url = f"{scheme}://{host}/robots.txt"
        cached = self.client.cache.get(robots_url)
        if cached and cached.get("stored_at"):
            stored = datetime.fromisoformat(cached["stored_at"].replace("Z", "+00:00"))
            if self.client.clock.now() - stored < timedelta(hours=24):
                # RFC 9309 allows caching robots.txt for up to 24 hours.
                text, _ = decode_body(cached["body"], cached.get("charset"), "text")
                policy = robots.parse(text)
                self.robots_evidence[host] = (
                    f"robots.txt from the local cache (read {cached['stored_at']}, under 24 h "
                    f"old; {len(policy.rules)} rule(s) for group {policy.matched_group})"
                )
                self._log(
                    robots_url,
                    "robots",
                    None,
                    "cache",
                    None,
                    None,
                    0,
                    "cached copy under 24 h old; no request",
                )
                return policy
        try:
            response = robots_url
            for _hop in range(self.client.policy.max_redirects + 1):
                response = self._request(
                    response, expect="robots", purpose="robots", conditional=True
                )
                if isinstance(response, Response):
                    break
                if check_url(response)[1] != host:
                    self.robots_evidence[host] = "robots.txt redirected to another host"
                    return robots.disallow_all("redirected off host")
                if scheme == "https" and urlsplit(response).scheme != "https":
                    self.robots_evidence[host] = "robots.txt redirected from https to http"
                    return robots.disallow_all("redirected to http")
        except (BudgetExhausted, UnsafeURL):
            raise
        except FetchError as exc:
            if exc.status is not None and 400 <= exc.status < 500 and exc.status != 429:
                # RFC 9309: an unavailable (4xx) robots file places no rules.
                self.robots_evidence[host] = f"no robots rules: {robots_url} returned {exc.status}"
                return robots.allow_all(f"HTTP {exc.status}")
            self.robots_evidence[host] = f"robots.txt unreadable ({exc.kind}); assumed disallow"
            return robots.disallow_all(exc.kind)
        if not isinstance(response, Response):
            self.robots_evidence[host] = "robots.txt redirected too many times"
            return robots.disallow_all("too many redirects")
        if response.status == 200 and not response.from_cache:
            self.client.cache.put(
                robots_url,
                {
                    "etag": response.headers.get("etag"),
                    "last_modified": response.headers.get("last-modified"),
                    "content_type": response.headers.get("content-type", ""),
                    "charset": None,
                    "final_url": response.final_url,
                    "stored_at": iso(self.client.clock.now()),
                    "source_id": self.source_id,
                },
                response.body,
            )
        policy = robots.parse(response.text)
        self.robots_evidence[host] = (
            f"robots.txt read ({len(policy.rules)} rule(s) for group {policy.matched_group})"
        )
        return policy

    def _spend(self) -> None:
        self.client.run_budget.check()
        self.budget.check()
        self.client.run_budget.spend()
        self.budget.spend()
        self.requests += 1

    def _request(self, url, *, expect, purpose, conditional):
        """One logical request with retries. Returns a Response, or a redirect
        target (str) for the caller to validate and follow."""
        client = self.client
        policy = client.policy
        scheme, host, port = check_url(url)
        cached = client.cache.get(url) if conditional else None
        attempt = 0
        while True:
            attempt += 1
            self._spend()
            try:
                address = safe_address(host, port, client.resolver)
            except UnsafeURL as exc:
                self._log(url, purpose, None, "refused", None, None, attempt, str(exc))
                raise
            except TransportFailure as exc:  # DNS failure: logged and retried
                self._log(url, purpose, None, "error", None, None, attempt, str(exc))
                if attempt > policy.max_retries:
                    raise
                client.timer.sleep(policy.backoff_seconds * (2 ** (attempt - 1)))
                continue
            headers = {
                "User-Agent": policy.user_agent,
                "Accept-Encoding": "gzip",
                "Accept": ", ".join(CONTENT_TYPES.get(expect, ("*/*",))) + ", */*;q=0.1",
                "Accept-Language": policy.accept_language,
            }
            if cached:
                if cached.get("etag"):
                    headers["If-None-Match"] = cached["etag"]
                if cached.get("last_modified"):
                    headers["If-Modified-Since"] = cached["last_modified"]
            client.limiter.wait(self.rate_group or host, self.interval)
            started = client.timer.monotonic()
            request = HttpRequest(
                url,
                host,
                port,
                scheme,
                address,
                headers,
                policy.connect_timeout,
                policy.read_timeout,
                policy.max_bytes,
            )
            try:
                raw = client.transport.send(request)
            except (ResponseTooLarge, RequestError) as exc:
                self._log(url, purpose, None, "error", None, None, attempt, str(exc))
                raise
            except TransportFailure as exc:
                self._log(url, purpose, None, "error", None, None, attempt, str(exc))
                if attempt > policy.max_retries:
                    raise
                client.timer.sleep(policy.backoff_seconds * (2 ** (attempt - 1)))
                continue
            elapsed = int((client.timer.monotonic() - started) * 1000)
            status = raw.status
            if status in REDIRECT_STATUSES:
                location = raw.headers.get("location")
                self._log(
                    url,
                    purpose,
                    status,
                    "redirect",
                    len(raw.body),
                    elapsed,
                    attempt,
                    f"to {location}",
                )
                if not location:
                    raise RedirectError(
                        "redirect without a Location header", url=url, status=status
                    )
                target = urljoin(url, location.strip())
                check_url(target)
                return target
            if status == 304:
                if not cached:
                    self._log(
                        url,
                        purpose,
                        status,
                        "error",
                        0,
                        elapsed,
                        attempt,
                        "304 without a cached copy",
                    )
                    raise UnexpectedContent(
                        "304 Not Modified without a cached copy", url=url, status=status
                    )
                self.not_modified += 1
                self._log(url, purpose, status, "not_modified", 0, elapsed, attempt)
                body = cached["body"]
                media, charset = cached.get("media_type", ""), cached.get("charset")
                text, encoding = decode_body(body, charset, expect)
                return Response(
                    url,
                    cached.get("final_url", url),
                    304,
                    {"content-type": cached.get("content_type", "")},
                    body,
                    text,
                    from_cache=True,
                    encoding=encoding,
                )
            if status in RETRY_STATUSES:
                wait = _retry_after(raw.headers.get("retry-after"), client.clock.now())
                self._log(
                    url,
                    purpose,
                    status,
                    "error",
                    len(raw.body),
                    elapsed,
                    attempt,
                    f"retry-after {raw.headers.get('retry-after')}",
                )
                if wait is not None and wait > policy.max_retry_after:
                    error = RateLimited if status == 429 else ServerError
                    raise error(
                        f"HTTP {status}; Retry-After {wait:.0f}s exceeds the "
                        f"{policy.max_retry_after:.0f}s cap; not waiting",
                        url=url,
                        status=status,
                    )
                if attempt > policy.max_retries:
                    error = RateLimited if status == 429 else ServerError
                    raise error(f"HTTP {status} after {attempt} attempt(s)", url=url, status=status)
                client.timer.sleep(
                    wait if wait is not None else policy.backoff_seconds * (2 ** (attempt - 1))
                )
                continue
            if status >= 400:
                self._log(url, purpose, status, "error", len(raw.body), elapsed, attempt)
                if status == 401:
                    raise AuthRequired(
                        "HTTP 401: authentication required; not attempted", url=url, status=status
                    )
                if status == 403:
                    raise AccessDenied(
                        "HTTP 403: access refused; not circumvented", url=url, status=status
                    )
                if status in {404, 410}:
                    raise NotFound(f"HTTP {status}: not found", url=url, status=status)
                raise FetchError(f"HTTP {status}", url=url, status=status)
            if status != 200:
                self._log(url, purpose, status, "error", len(raw.body), elapsed, attempt)
                raise FetchError(f"unexpected HTTP {status}", url=url, status=status)
            body = raw.body
            encoding_header = raw.headers.get("content-encoding", "").lower().strip()
            if encoding_header in {"gzip", "x-gzip"}:
                try:
                    body = _gunzip(body, policy.max_bytes, url)
                except FetchError as exc:
                    self._log(
                        url, purpose, status, "error", len(raw.body), elapsed, attempt, str(exc)
                    )
                    raise
            elif encoding_header not in {"", "identity"}:
                self._log(
                    url,
                    purpose,
                    status,
                    "error",
                    len(raw.body),
                    elapsed,
                    attempt,
                    f"content-encoding {encoding_header}",
                )
                raise UnexpectedContent(
                    f"unsupported content-encoding {encoding_header!r}", url=url, status=status
                )
            media, charset = _media_type(raw.headers)
            self._validate_content(url, expect, media, body, status, purpose, elapsed, attempt)
            text, encoding = decode_body(body, charset, expect)
            self._log(url, purpose, status, "ok", len(body), elapsed, attempt)
            if conditional and (raw.headers.get("etag") or raw.headers.get("last-modified")):
                client.cache.put(
                    url,
                    {
                        "etag": raw.headers.get("etag"),
                        "last_modified": raw.headers.get("last-modified"),
                        "content_type": raw.headers.get("content-type", ""),
                        "media_type": media,
                        "charset": charset,
                        "final_url": url,
                        "stored_at": iso(client.clock.now()),
                        "source_id": self.source_id,
                    },
                    body,
                )
            return Response(url, url, status, raw.headers, body, text, encoding=encoding)

    def _validate_content(self, url, expect, media, body, status, purpose, elapsed, attempt):
        if expect == "robots":
            if _HTML_START.match(body[:200]):
                self._log(
                    url,
                    purpose,
                    status,
                    "error",
                    len(body),
                    elapsed,
                    attempt,
                    "HTML where robots.txt expected",
                )
                raise UnexpectedContent("HTML returned for robots.txt", url=url, status=status)
            return
        allowed = CONTENT_TYPES.get(expect)
        problem = None
        if allowed and media not in allowed and not (expect == "json" and media.endswith("+json")):
            problem = f"content type {media or 'missing'!r}, expected {expect}"
        elif expect in {"xml", "json"} and _HTML_START.match(body[:200]):
            problem = f"an HTML page was returned where {expect} was expected"
        if problem:
            self._log(url, purpose, status, "error", len(body), elapsed, attempt, problem)
            raise UnexpectedContent(problem, url=url, status=status)

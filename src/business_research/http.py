from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

from business_research import robots

log = logging.getLogger("business_research")

PRODUCT = "business-research-tool"
VERSION = "0.1"
DEFAULT_CONTACT = "https://github.com/chasebuilds/business-research-tool"
# The floor is not configurable: no setting can make this client faster than this per host.
MIN_INTERVAL_FLOOR = 0.5
DEFAULT_INTERVAL = 1.0
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_WAIT = 120.0
# This many 403 or 429 answers in a row from one host means it does not want this client.
BLOCK_STREAK = 3
BLOCK_STATUSES = {403, 429}
ROBOTS_TTL = 24 * 3600
DEFAULT_TTL = 7 * 24 * 3600


class FetchError(RuntimeError):
    pass


class RobotsDisallowed(FetchError):
    pass


class OfflineMiss(FetchError):
    """Offline mode and the answer is not in the cache. Nothing was sent."""


class HostBlocked(FetchError):
    """The host signalled a block, or asked for a longer wait than this run allows.
    Nothing more is sent to it for the rest of the run."""


@dataclass(frozen=True)
class ApiExemption:
    """One documented API endpoint that may be called although robots.txt disallows its path.

    robots.txt speaks to crawlers. Some operators disallow a whole path there and, in their API
    terms, invite automated clients to one endpoint under it. An exemption names exactly one
    host and one path prefix, and carries the terms page that grants the access. It is never a
    wildcard, it is logged on every use, and it does not apply when robots.txt cannot be read.
    """

    host: str
    path_prefix: str
    terms_url: str
    reason: str


@dataclass
class Response:
    status: int
    data: Any  # parsed JSON: shape depends on the source
    url: str
    retrieved: str
    from_cache: bool


def default_user_agent() -> str:
    """Names the tool and a project contact URL. Never a person's name or email."""
    contact = os.environ.get("BRT_CONTACT", "").strip() or DEFAULT_CONTACT
    return f"{PRODUCT}/{VERSION} ({contact})"


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class PoliteClient:
    """GET client that checks robots.txt, spaces requests per host, backs off and caches to disk."""

    def __init__(
        self,
        cache_dir: str | Path = ".cache",
        user_agent: str | None = None,
        min_interval: float = DEFAULT_INTERVAL,
        host_intervals: dict[str, float] | None = None,
        host_user_agents: dict[str, str] | None = None,
        max_retries: int = 4,
        cache_ttl: float = DEFAULT_TTL,
        refresh: bool = False,
        offline: bool = False,
        session=None,
        sleep=time.sleep,
        clock=time.monotonic,
        wall_clock=time.time,
    ):
        if offline and refresh:
            raise ValueError("offline and refresh cannot be combined: refresh needs the network")
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent or default_user_agent()
        self.min_interval = max(MIN_INTERVAL_FLOOR, float(min_interval))
        self.host_intervals = {
            h: max(MIN_INTERVAL_FLOOR, float(v)) for h, v in (host_intervals or {}).items()
        }
        self.host_user_agents = dict(host_user_agents or {})
        self.max_retries = max_retries
        self.cache_ttl = cache_ttl
        self.refresh = refresh
        # Offline answers only from the cache, at any age, and sends nothing, not even robots.txt.
        self.offline = offline
        self._session = session
        self._sleep = sleep
        self._clock = clock
        self._wall_clock = wall_clock
        self._last: dict[str, float] = {}
        self._robots: dict[str, tuple[robots.RobotsRules, float]] = {}
        self._streak: Counter[str] = Counter()
        self.blocked: dict[str, str] = {}
        self.exemptions: list[ApiExemption] = []
        self.stats: Counter[str] = Counter()

    @property
    def session(self):
        if self._session is None:
            import requests

            self._session = requests.Session()
        return self._session

    def interval_for(self, host: str) -> float:
        return self.host_intervals.get(host, self.min_interval)

    def set_interval(self, host: str, seconds: float) -> None:
        """Pace one host. The floor still applies."""
        self.host_intervals[host] = max(MIN_INTERVAL_FLOOR, float(seconds))

    def get_json(self, url: str, params: dict[str, str] | None = None) -> Response:
        """Fetch JSON. A 404 comes back as a Response with data None, other failures raise."""
        full = f"{url}?{urlencode(params)}" if params else url
        if not self.offline:
            self._check_robots(full)
        cached = self._cache_read(full, None if self.offline else self.cache_ttl)
        if cached is not None:
            self.stats["cache_hits"] += 1
            log.info("cache  %s (fetched %s)", full, cached["retrieved"])
            data = None if cached["status"] == 404 else _loads(cached["body"])
            return Response(cached["status"], data, full, cached["retrieved"], True)
        if self.offline:
            self.stats["offline_misses"] += 1
            log.warning("offline, not cached  %s", full)
            raise OfflineMiss(f"offline and not in cache: {full}")
        status, body, retrieved = self._fetch(full)
        if status not in (200, 404):
            raise FetchError(f"HTTP {status} from {full}")
        # A 404 is cached too, so a rerun does not ask again for a record that does not exist.
        self._cache_write(full, status, body, retrieved)
        if status == 404:
            return Response(status, None, full, retrieved, False)
        return Response(status, _loads(body), full, retrieved, False)

    def _check_robots(self, full_url: str) -> None:
        parts = urlsplit(full_url)
        origin = f"{parts.scheme}://{parts.netloc}"
        rules, loaded = self._robots.get(origin, (None, 0.0))
        # Held in memory only, so every run reads the live file, and a long run reads it again
        # once a day.
        if rules is None or self._clock() - loaded > ROBOTS_TTL:
            rules = self._load_robots(origin)
            self._robots[origin] = (rules, self._clock())
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        if not rules.allowed(PRODUCT, path):
            granted = next(
                (
                    e
                    for e in self.exemptions
                    if not rules.deny_all and e.host == parts.netloc and path.startswith(e.path_prefix)
                ),
                None,
            )
            if granted:
                self.stats["robots_exemptions_used"] += 1
                log.info("exempt %s (robots.txt disallows it, allowed by %s)", full_url, granted.terms_url)
                return
            self.stats["robots_refused"] += 1
            log.warning("robots refused  %s", full_url)
            raise RobotsDisallowed(f"robots.txt at {origin} disallows {path}")

    def _load_robots(self, origin: str) -> robots.RobotsRules:
        url = origin + "/robots.txt"
        try:
            status, body, _ = self._fetch(url, is_robots=True)
        except FetchError as exc:
            log.warning("robots.txt unreachable at %s (%s): treating host as disallowed", origin, exc)
            return robots.RobotsRules(deny_all=True)
        log.info("robots %s -> HTTP %s", url, status)
        return robots.from_status(status, body)

    def _block(self, host: str, reason: str) -> None:
        self.blocked[host] = reason
        self.stats["hosts_blocked"] += 1
        log.error("stop   %s: %s. No more requests to this host in this run.", host, reason)
        raise HostBlocked(f"{host} skipped for the rest of the run: {reason}")

    def _fetch(self, full_url: str, is_robots: bool = False) -> tuple[int, str, str]:
        host = urlsplit(full_url).netloc
        if host in self.blocked:
            raise HostBlocked(f"{host} skipped for the rest of the run: {self.blocked[host]}")
        headers = {
            "User-Agent": self.host_user_agents.get(host, self.user_agent),
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        }
        for attempt in range(self.max_retries + 1):
            self._wait_turn(host)
            try:
                reply = self.session.get(full_url, headers=headers, timeout=30)
                status, retry_after = reply.status_code, reply.headers.get("Retry-After")
            except OSError as exc:
                status, retry_after = 0, None
                log.warning("error  %s (%s)", full_url, exc)
            self.stats["requests"] += 1
            self.stats[f"requests:{host}"] += 1
            # A missing robots.txt often answers 403 on API hosts. That is not a block signal.
            if status in BLOCK_STATUSES and not is_robots:
                self._streak[host] += 1
                if self._streak[host] >= BLOCK_STREAK:
                    self._block(host, f"{BLOCK_STREAK} answers of HTTP 403 or 429 in a row")
            elif status and status < 500:
                self._streak[host] = 0
            if status and status not in RETRY_STATUSES:
                log.info("GET    %s -> HTTP %s", full_url, status)
                return status, reply.text, utc_now()
            if attempt == self.max_retries:
                break
            asked = retry_after_seconds(retry_after)
            if asked is not None and asked > MAX_RETRY_WAIT:
                # Waiting that long would stall the run and coming back early would break the
                # server's request, so the host is dropped.
                self._block(host, f"Retry-After asked for {asked:.0f}s, over the {MAX_RETRY_WAIT:.0f}s cap")
            wait = asked if asked is not None else min(MAX_RETRY_WAIT, 5.0 * 2**attempt)
            self.stats["retries"] += 1
            log.warning(
                "retry  %s -> HTTP %s, waiting %.0fs (attempt %d)", full_url, status, wait, attempt + 1
            )
            self._sleep(wait)
        raise FetchError(f"gave up on {full_url} after {self.max_retries + 1} attempts")

    def _wait_turn(self, host: str) -> None:
        last = self._last.get(host)
        if last is not None:
            remaining = self.interval_for(host) - (self._clock() - last)
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()

    def _cache_path(self, full_url: str) -> Path:
        return self.cache_dir / (hashlib.sha256(full_url.encode()).hexdigest()[:32] + ".json")

    def _cache_read(self, full_url: str, ttl: float | None) -> dict | None:
        """The cached entry, or None when absent, expired (past `ttl` seconds) or unreadable."""
        path = self._cache_path(full_url)
        if self.refresh or not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            saved = float(entry["saved"])
            if not all(key in entry for key in ("status", "retrieved", "body")):
                raise KeyError("missing fields")
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # A half-written or hand-edited file is a miss, not a crash. It is overwritten on refetch.
            log.warning("cache entry unreadable, ignored: %s (%s)", path.name, exc)
            return None
        if entry.get("url", full_url) != full_url:
            return None
        if ttl is not None and self._wall_clock() - saved > ttl:
            return None
        return entry

    def _cache_write(self, full_url: str, status: int, body: str, retrieved: str) -> None:
        entry = {
            "url": full_url,
            "status": status,
            "retrieved": retrieved,
            "saved": self._wall_clock(),
            "body": body,
        }
        self._cache_path(full_url).write_text(json.dumps(entry), encoding="utf-8")


def _loads(body: str) -> Any:
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise FetchError(f"response is not JSON: {exc}") from exc


def retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds a Retry-After header asks for, given as a number or an HTTP date. None when the
    header is absent or unreadable. Never shortened: the caller waits the full time or gives up
    on the host."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())

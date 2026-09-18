"""arXiv Atom API source: the primary, key-free source (AGENTS.md §7).

Parsing is a pure function so it can be tested straight from a recorded
response. URLs are always taken from the response itself — an ``<id>`` or a
``rel="alternate"`` link — and never rebuilt from the identifier.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime
from xml.etree import ElementTree

import httpx
from pydantic import ValidationError

from briefing.errors import SourceError
from briefing.net import parse_retry_after
from briefing.schemas import Paper
from briefing.sources.base import USER_AGENT, FetchParams
from briefing.sources.cache import CacheStore

ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"

# arXiv's CDN answers bursty clients with 406 (it uses that instead of 429), so
# 406 is treated as a rate limit rather than a permanent client error.
RETRYABLE_STATUS = frozenset({406, 429, 500, 502, 503, 504})

# arXiv asks for roughly one request every few seconds from a single client.
DEFAULT_MIN_INTERVAL_S = 3.0
DEFAULT_MAX_ATTEMPTS = 4
# Once arXiv pushes back it stays unhappy for a while. Retrying every second
# (and having every concurrent query retry separately) only extends that, so a
# single cooldown is shared by all in-flight requests.
COOLDOWN_BASE_S = 20.0
MAX_COOLDOWN_S = 120.0

# A rate limit is an IP-level penalty, not a hiccup: give it one quick retry,
# then stop asking for the rest of the run and let another source answer.
RATE_LIMIT_STATUS = frozenset({406, 429})
RATE_LIMIT_ATTEMPTS = 2
RATE_LIMIT_COOLDOWN_S = 5.0

logger = logging.getLogger(__name__)

_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV = "http://arxiv.org/schemas/atom"

# arXiv began in 1991; these bound an open-ended time window.
_EARLIEST_DATE = "199101010000"
_LATEST_DATE = "299912312359"


def _text(node: ElementTree.Element | None) -> str:
    """Flatten an element's text, collapsing the newlines arXiv pads with."""
    if node is None or node.text is None:
        return ""
    return " ".join(node.text.split())


def _normalize_arxiv_id(raw_id: str) -> str:
    """``http://arxiv.org/abs/2401.01234v1`` -> ``2401.01234``."""
    identifier = raw_id.strip()
    for prefix in ("https://arxiv.org/abs/", "http://arxiv.org/abs/"):
        if identifier.startswith(prefix):
            identifier = identifier[len(prefix) :]
            break
    if "v" in identifier:
        head, _, tail = identifier.rpartition("v")
        if head and tail.isdigit():
            identifier = head
    return identifier


def _alternate_url(entry: ElementTree.Element) -> str:
    """Pick the human-readable landing page, preferring ``text/html``."""
    fallback = ""
    for link in entry.findall(f"{{{_ATOM}}}link"):
        if link.get("rel") != "alternate":
            continue
        href = link.get("href") or ""
        if not href:
            continue
        if link.get("type") == "text/html":
            return href
        fallback = fallback or href
    return fallback


def parse_atom(
    xml_text: str,
    *,
    retrieved_at: datetime,
    origin: str,
) -> tuple[list[Paper], dict[str, int]]:
    """Convert an arXiv Atom feed into ``Paper`` records.

    Returns the parsed papers plus a count of why entries were dropped, so the
    caller can report data loss instead of quietly returning a short list.
    """
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise SourceError(f"source returned malformed XML: {exc}") from exc

    papers: list[Paper] = []
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for entry in root.iter(f"{{{_ATOM}}}entry"):
        raw_id = _text(entry.find(f"{{{_ATOM}}}id"))
        url = _alternate_url(entry) or raw_id
        if not url:
            drop("missing_url")
            continue

        arxiv_id = _normalize_arxiv_id(raw_id)
        if not arxiv_id:
            drop("missing_arxiv_id")
            continue

        published = _text(entry.find(f"{{{_ATOM}}}published"))
        year = int(published[:4]) if published[:4].isdigit() else None
        journal_ref = _text(entry.find(f"{{{_ARXIV}}}journal_ref"))
        doi = _text(entry.find(f"{{{_ARXIV}}}doi")) or None

        try:
            papers.append(
                Paper(
                    paper_id=f"arxiv:{arxiv_id}",
                    title=_text(entry.find(f"{{{_ATOM}}}title")),
                    authors=[
                        _text(author.find(f"{{{_ATOM}}}name"))
                        for author in entry.findall(f"{{{_ATOM}}}author")
                    ],
                    year=year,
                    venue=journal_ref or origin,
                    origin=origin,
                    url=url,
                    doi=doi,
                    arxiv_id=arxiv_id,
                    abstract=_text(entry.find(f"{{{_ATOM}}}summary")),
                    citation_count=None,
                    open_access=True,
                    retrieved_at=retrieved_at,
                )
            )
        except ValidationError:
            drop("invalid_metadata")

    return papers, dropped


class ArxivSource:
    """Queries the arXiv Atom API through the raw-response cache."""

    name = "arxiv"

    def __init__(
        self,
        *,
        cache: CacheStore,
        http_client: httpx.AsyncClient | None = None,
        endpoint: str = ARXIV_ENDPOINT,
        user_agent: str = USER_AGENT,
        timeout_s: float = 30.0,
        min_interval_s: float = DEFAULT_MIN_INTERVAL_S,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._cache = cache
        self._endpoint = endpoint
        self._user_agent = user_agent
        # arXiv asks for "no more than one request every three seconds, and
        # limit requests to a single connection at a time" (API terms of use),
        # so the pool is capped at one connection as well as being serialised.
        self._http = http_client or httpx.AsyncClient(
            timeout=timeout_s,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
        self._owns_http = http_client is None
        self._min_interval = min_interval_s
        self._max_attempts = max(1, max_attempts)
        self._sleep = sleep or asyncio.sleep
        self._monotonic = monotonic or time.monotonic
        self._last_request_at: float | None = None
        self._blocked_until: float | None = None
        self._rate_limited = False
        # Spent run-wide, not per query: a rate limit should cost a couple of
        # requests in total, not a couple per query.
        self._rate_limit_budget = RATE_LIMIT_ATTEMPTS
        self._throttle = asyncio.Lock()
        self.dropped: dict[str, int] = {}

    @property
    def rate_limited(self) -> bool:
        """True once this source gave up on the rest of the run."""
        return self._rate_limited

    async def aclose(self) -> None:
        if self._owns_http:
            await self._http.aclose()

    async def search(self, query: str, params: FetchParams) -> list[Paper]:
        self.dropped = {}
        request_params = self._request_params(query, params)
        entry = await self._cache.get_or_fetch(
            source=self.name,
            url=self._endpoint,
            query=query,
            params=request_params,
            fetch=lambda: self._fetch(request_params),
        )
        papers, dropped = parse_atom(entry.body, retrieved_at=entry.fetched_at, origin=self.name)
        self.dropped = dropped
        return papers

    # --- internals ----------------------------------------------------------

    def _request_params(self, query: str, params: FetchParams) -> dict[str, str]:
        search_query = query
        if params.time_window is not None:
            start_year, end_year = params.time_window
            lower = f"{start_year}01010000" if start_year else _EARLIEST_DATE
            upper = f"{end_year}12312359" if end_year else _LATEST_DATE
            search_query = f"({query}) AND submittedDate:[{lower} TO {upper}]"

        return {
            "search_query": search_query,
            "start": str(params.start),
            "max_results": str(params.capped_max_results),
            "sortBy": params.sort_by,
            "sortOrder": "descending",
        }

    async def _request(self, request_params: dict[str, str]) -> httpx.Response:
        """Issue one request while holding the turn.

        Waiting and sending happen under the same lock, so there is never more
        than one request in flight and consecutive requests are at least
        ``min_interval`` apart, as the terms of use require. A cooldown set by
        one query therefore also holds the others back.
        """
        async with self._throttle:
            # Checked here as well as in the retry loop: queries that were
            # already in flight when the breaker tripped must not send either.
            if self._rate_limited:
                raise SourceError(
                    "arxiv is rate-limiting this client; skipping the rest of "
                    "this run (another source will answer these queries)"
                )
            now = self._monotonic()
            if self._blocked_until is not None and self._blocked_until > now:
                await self._sleep(self._blocked_until - now)
                now = self._monotonic()
            if self._last_request_at is not None:
                remaining = self._min_interval - (now - self._last_request_at)
                if remaining > 0:
                    await self._sleep(remaining)
            self._last_request_at = self._monotonic()
            logger.debug("arxiv request", extra={"query": request_params.get("search_query", "")})
            response = await self._http.get(
                self._endpoint,
                params=request_params,
                headers={"User-Agent": self._user_agent},
            )
            # Account for a rate limit while still holding the lock, so the
            # budget is shared even when several queries run concurrently.
            if response.status_code in RATE_LIMIT_STATUS:
                self._rate_limit_budget -= 1
                if self._rate_limit_budget <= 0:
                    self._rate_limited = True
            return response

    async def _backoff(
        self,
        attempt: int,
        retry_after: float | None,
        *,
        short: bool = False,
    ) -> None:
        """Wait out a failure, and make every other query wait too."""
        if short:
            # An explicit Retry-After is honoured; otherwise do not linger, the
            # penalty will not clear in seconds anyway.
            delay = retry_after if retry_after is not None else RATE_LIMIT_COOLDOWN_S
            delay = min(delay, MAX_COOLDOWN_S)
        else:
            delay = retry_after or COOLDOWN_BASE_S * 2.0 ** (attempt - 1)
            delay = min(delay, MAX_COOLDOWN_S)
        # Publish the cooldown before sleeping: concurrent queries then wait it
        # out instead of each retrying into the same blocked window.
        self._blocked_until = max(self._blocked_until or 0.0, self._monotonic() + delay)
        await self._sleep(delay)

    async def _fetch(self, request_params: dict[str, str]) -> str:
        """Fetch with spacing and bounded retries.

        A 406 from arXiv means "you are asking too often", not "your request is
        wrong", so it is retried after a pause instead of failing the query.
        """
        last_error = "no attempt was made"
        rate_limited = False
        for attempt in range(1, self._max_attempts + 1):
            if self._rate_limited:
                raise SourceError(
                    "arxiv is rate-limiting this client; skipping the rest of "
                    "this run (another source will answer these queries)"
                )
            try:
                response = await self._request(request_params)
            except httpx.HTTPError as exc:
                last_error = f"request failed: {exc}"
                if attempt < self._max_attempts:
                    await self._backoff(attempt, None)
                    continue
                break

            if response.status_code in RETRYABLE_STATUS:
                last_error = f"arxiv returned HTTP {response.status_code}"
                status_is_rate_limit = response.status_code in RATE_LIMIT_STATUS
                rate_limited = rate_limited or status_is_rate_limit
                allowed = self._max_attempts
                if status_is_rate_limit and self._rate_limited:
                    break
                if attempt < allowed:
                    logger.warning(
                        "arxiv rate-limited or unavailable; backing off",
                        extra={
                            "status": response.status_code,
                            "attempt": attempt,
                        },
                    )
                    retry_after = parse_retry_after(response.headers.get("Retry-After"))
                    await self._backoff(attempt, retry_after, short=status_is_rate_limit)
                    continue
                break

            if response.status_code >= 400:
                raise SourceError(f"arxiv returned HTTP {response.status_code}")
            return response.text

        message = f"arxiv did not answer after {self._max_attempts} attempts: {last_error}"
        if rate_limited:
            self._rate_limited = True
            message += (
                ". HTTP 406/429 from arXiv means this client is being rate-limited "
                "for a while: wait a few minutes, and drop --no-cache so repeated "
                "runs reuse the cached response instead of asking again"
            )
        raise SourceError(message)

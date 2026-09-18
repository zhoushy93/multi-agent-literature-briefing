"""arXiv Atom API source: the primary, key-free source (AGENTS.md §7).

Parsing is a pure function so it can be tested straight from a recorded
response. URLs are always taken from the response itself — an ``<id>`` or a
``rel="alternate"`` link — and never rebuilt from the identifier.
"""

from __future__ import annotations

from datetime import datetime
from xml.etree import ElementTree

import httpx
from pydantic import ValidationError

from briefing.errors import SourceError
from briefing.schemas import Paper
from briefing.sources.base import USER_AGENT, FetchParams
from briefing.sources.cache import CacheStore

ARXIV_ENDPOINT = "https://export.arxiv.org/api/query"

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
    ) -> None:
        self._cache = cache
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._http = http_client or httpx.AsyncClient(timeout=timeout_s)
        self._owns_http = http_client is None
        self.dropped: dict[str, int] = {}

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

    async def _fetch(self, request_params: dict[str, str]) -> str:
        try:
            response = await self._http.get(
                self._endpoint,
                params=request_params,
                headers={"User-Agent": self._user_agent},
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"arxiv request failed: {exc}") from exc

        if response.status_code >= 400:
            raise SourceError(f"arxiv returned HTTP {response.status_code}")
        return response.text

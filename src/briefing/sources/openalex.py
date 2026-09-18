"""OpenAlex source: a free, key-less fallback for when arXiv pushes back.

arXiv rate-limits by IP and answers with 406; a single source that can block a
whole run is a single point of failure. OpenAlex needs no API key, covers the
same literature, and is generous with rate limits.

Supplying ``mailto`` puts the client in OpenAlex's "polite pool", which is
their documented way to get better service; it is optional and comes from
``BRIEFING_OPENALEX_MAILTO``.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from briefing.errors import SourceError
from briefing.schemas import Paper
from briefing.sources.base import USER_AGENT, FetchParams
from briefing.sources.cache import CacheStore

logger = logging.getLogger(__name__)

OPENALEX_ENDPOINT = "https://api.openalex.org/works"

# OpenAlex rejects per-page above this.
MAX_PER_PAGE = 100


def strip_doi_prefix(doi: str) -> str:
    """``https://doi.org/10.1/x`` -> ``10.1/x`` (the form used elsewhere)."""
    value = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    return value


def abstract_from_inverted_index(index: Mapping[str, Any] | None) -> str:
    """Rebuild an abstract from OpenAlex's word -> positions map."""
    if not index:
        return ""
    positions: list[tuple[int, str]] = []
    for word, places in index.items():
        for place in places or []:
            positions.append((int(place), str(word)))
    if not positions:
        return ""
    positions.sort()
    return " ".join(word for _, word in positions)


def parse_works(
    payload: Mapping[str, Any],
    *,
    retrieved_at: datetime,
    origin: str,
) -> tuple[list[Paper], dict[str, int]]:
    """Convert an OpenAlex works response into ``Paper`` records."""
    papers: list[Paper] = []
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for work in payload.get("results") or []:
        title = str(work.get("title") or "").strip()
        if not title:
            drop("missing_title")
            continue

        location = work.get("primary_location") or {}
        url = str(location.get("landing_page_url") or work.get("doi") or work.get("id") or "")
        if not url:
            drop("missing_url")
            continue

        doi = strip_doi_prefix(str(work["doi"])) if work.get("doi") else None
        openalex_id = str(work.get("id") or "").rsplit("/", 1)[-1]
        paper_id = f"doi:{doi}" if doi else f"openalex:{openalex_id}"
        if not openalex_id and not doi:
            drop("missing_identifier")
            continue

        authors = [
            str((authorship.get("author") or {}).get("display_name") or "").strip()
            for authorship in work.get("authorships") or []
        ]
        authors = [author for author in authors if author]
        if not authors:
            drop("missing_authors")
            continue

        source = location.get("source") or {}
        papers.append(
            Paper(
                paper_id=paper_id,
                title=title,
                authors=authors,
                year=work.get("publication_year"),
                venue=str(source.get("display_name") or "") or None,
                origin=origin,
                url=url,
                doi=doi,
                arxiv_id=None,
                abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")),
                citation_count=work.get("cited_by_count"),
                open_access=bool(work.get("best_oa_location") or location.get("is_oa") or False),
                retrieved_at=retrieved_at,
            )
        )

    return papers, dropped


class OpenAlexSource:
    """Queries OpenAlex through the same raw-response cache as arXiv."""

    name = "openalex"

    def __init__(
        self,
        *,
        cache: CacheStore,
        http_client: httpx.AsyncClient | None = None,
        endpoint: str = OPENALEX_ENDPOINT,
        user_agent: str = USER_AGENT,
        mailto: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._cache = cache
        self._endpoint = endpoint
        self._user_agent = user_agent
        self._mailto = mailto
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
        payload = _loads(entry.body)
        papers, dropped = parse_works(
            payload,
            retrieved_at=entry.fetched_at,
            origin=self.name,
        )
        self.dropped = dropped
        return papers

    # --- internals ----------------------------------------------------------

    def _request_params(self, query: str, params: FetchParams) -> dict[str, str]:
        request: dict[str, str] = {
            "search": query,
            "per-page": str(min(params.capped_max_results, MAX_PER_PAGE)),
            "sort": "relevance_score:desc",
        }
        start_year, end_year = params.time_window or (None, None)
        filters: list[str] = []
        if start_year:
            filters.append(f"from_publication_date:{start_year}-01-01")
        if end_year:
            filters.append(f"to_publication_date:{end_year}-12-31")
        if filters:
            request["filter"] = ",".join(filters)
        if self._mailto:
            request["mailto"] = self._mailto
        return request

    async def _fetch(self, request_params: dict[str, str]) -> str:
        try:
            response = await self._http.get(
                self._endpoint,
                params=request_params,
                headers={"User-Agent": self._user_agent},
            )
        except httpx.HTTPError as exc:
            raise SourceError(f"openalex request failed: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(f"openalex returned HTTP {response.status_code}")
        return response.text


def _loads(body: str) -> Mapping[str, Any]:
    import json

    try:
        payload = json.loads(body)
    except ValueError as exc:
        raise SourceError(f"openalex returned malformed JSON: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SourceError("openalex returned an unexpected JSON shape")
    return payload

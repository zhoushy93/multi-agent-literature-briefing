"""S2 Retriever: turn a plan's queries into a candidate list.

No LLM here. Queries run concurrently, results are merged in plan order, and
every source failure is recorded rather than thrown away.

The retriever also exists to keep a single source from sinking a run: when a
query's own source fails, the query is retried against the other configured
sources. That is what makes arXiv's IP-based rate limiting (HTTP 406) a
degradation instead of a failure — see docs/decisions/004-arxiv-rate-limits.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence

from briefing.errors import SourceError
from briefing.schemas import CacheStats, CandidateList, Paper, SearchPlan
from briefing.sources.base import DEFAULT_MAX_RESULTS, FetchParams, Source
from briefing.sources.cache import CacheStore

logger = logging.getLogger(__name__)

_FIELD_PREFIXES = re.compile(r"\b(all|abs|ti|au|cat|co|jr|rn|id|doi):", re.IGNORECASE)
_OPERATORS = re.compile(r"\b(AND|OR|ANDNOT)\b", re.IGNORECASE)
_PUNCTUATION = str.maketrans({character: " " for character in '"()[]{}'})


def to_plain_query(query: str) -> str:
    """Reduce a source-specific query to free text for a different source.

    ``all:"diffusion model" AND all:"climate modeling"`` becomes
    ``diffusion model climate modeling``. A fallback query is a degradation, so
    it is kept deliberately simple rather than translated.
    """
    text = _FIELD_PREFIXES.sub(" ", query)
    text = _OPERATORS.sub(" ", text)
    return " ".join(text.translate(_PUNCTUATION).split())


class Retriever:
    """S2: runs the plan against the configured sources."""

    name = "retriever"

    def __init__(
        self,
        *,
        sources: Sequence[Source],
        cache: CacheStore,
        max_concurrency: int = 4,
        max_results: int = DEFAULT_MAX_RESULTS,
        allow_fallback: bool = True,
    ) -> None:
        self._sources = {source.name: source for source in sources}
        self._cache = cache
        self._max_concurrency = max_concurrency
        self._max_results = max_results
        self._allow_fallback = allow_fallback

    async def run(self, plan: SearchPlan) -> CandidateList:
        gate = asyncio.Semaphore(self._max_concurrency)
        dropped: dict[str, int] = {}
        papers: list[Paper] = []
        queries_used: list[str] = []
        failures: list[str] = []

        pairs: list[tuple[str, str]] = []
        for query in plan.queries:
            if query.source not in self._sources:
                dropped["source_unavailable"] = dropped.get("source_unavailable", 0) + 1
                logger.warning(
                    "skipping query for an unconfigured source",
                    extra={"source": query.source, "query": query.q},
                )
                continue
            queries_used.append(f"{query.source}: {query.q}")
            pairs.append((query.source, query.q))

        per_source_counts: dict[str, int] = dict.fromkeys(self._sources, 0)
        collected, failed_pairs = await self._collect(
            pairs,
            gate,
            per_source_counts,
            failures,
        )
        papers.extend(collected)

        if self._allow_fallback and failed_pairs:
            papers.extend(
                await self._fallback(
                    failed_pairs,
                    gate,
                    per_source_counts,
                    failures,
                    queries_used,
                    dropped,
                )
            )

        if not papers and failures:
            raise SourceError("every query failed: " + "; ".join(failures))

        for source in self._sources.values():
            source_dropped = getattr(source, "dropped", None)
            if source_dropped:
                for key, value in source_dropped.items():
                    dropped[key] = dropped.get(key, 0) + value
            if getattr(source, "rate_limited", False):
                dropped["source_rate_limited"] = dropped.get("source_rate_limited", 0) + 1

        return CandidateList(
            papers=papers,
            queries_used=queries_used,
            per_source_counts=per_source_counts,
            cache=self._cache.stats() or CacheStats(),
            dropped=dropped,
        )

    # --- internals ----------------------------------------------------------

    async def _search(
        self,
        source_name: str,
        query: str,
        gate: asyncio.Semaphore,
    ) -> list[Paper]:
        async with gate:
            return await self._sources[source_name].search(
                query,
                FetchParams(max_results=self._max_results),
            )

    async def _collect(
        self,
        pairs: Sequence[tuple[str, str]],
        gate: asyncio.Semaphore,
        per_source_counts: dict[str, int],
        failures: list[str],
    ) -> tuple[list[Paper], list[tuple[str, str]]]:
        """Run the given (source, query) pairs, returning papers and failures."""
        if not pairs:
            return [], []
        results = await asyncio.gather(
            *(self._search(name, query, gate) for name, query in pairs),
            return_exceptions=True,
        )
        papers: list[Paper] = []
        failed: list[tuple[str, str]] = []
        for (source_name, query), result in zip(pairs, results, strict=True):
            if isinstance(result, BaseException):
                failures.append(str(result))
                failed.append((source_name, query))
                continue
            papers.extend(result)
            per_source_counts[source_name] = per_source_counts.get(source_name, 0) + len(result)
        return papers, failed

    async def _fallback(
        self,
        failed_pairs: Sequence[tuple[str, str]],
        gate: asyncio.Semaphore,
        per_source_counts: dict[str, int],
        failures: list[str],
        queries_used: list[str],
        dropped: dict[str, int],
    ) -> list[Paper]:
        """Re-ask the failed queries of the other configured sources."""
        attempts: list[tuple[str, str]] = []
        for failed_source, query in failed_pairs:
            plain = to_plain_query(query)
            if not plain:
                continue
            for name in self._sources:
                if name != failed_source:
                    attempts.append((name, plain))

        if not attempts:
            return []

        logger.warning(
            "retrying failed queries on another source",
            extra={"attempts": len(attempts)},
        )
        dropped["source_fallback"] = dropped.get("source_fallback", 0) + len(attempts)
        queries_used.extend(f"{name}: {query} (fallback)" for name, query in attempts)
        papers, still_failing = await self._collect(
            attempts,
            gate,
            per_source_counts,
            failures,
        )
        if still_failing:
            logger.warning(
                "fallback also failed",
                extra={"queries": [query for _, query in still_failing]},
            )
        return papers

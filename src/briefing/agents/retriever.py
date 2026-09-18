"""S2 Retriever: turn a plan's queries into a candidate list.

No LLM here. Queries run concurrently under a semaphore, results are merged in
plan order, and every source failure is recorded rather than thrown away.

Degradation rules (docs/architecture.md §4, S2):

* a query naming a source that is not configured is skipped and counted;
* a source that fails is counted, and the other sources still run;
* if nothing was retrieved *and* something failed, the stage fails — an empty
  candidate list caused by a broken source must not look like "no such paper".
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from briefing.errors import SourceError
from briefing.schemas import CacheStats, CandidateList, Paper, SearchPlan
from briefing.sources.base import DEFAULT_MAX_RESULTS, FetchParams, Source
from briefing.sources.cache import CacheStore

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._sources = {source.name: source for source in sources}
        self._cache = cache
        self._max_concurrency = max_concurrency
        self._max_results = max_results

    async def run(self, plan: SearchPlan) -> CandidateList:
        gate = asyncio.Semaphore(self._max_concurrency)
        dropped: dict[str, int] = {}
        per_source_counts: dict[str, int] = {}
        papers: list[Paper] = []
        queries_used: list[str] = []
        failures: list[str] = []

        async def run_query(source_name: str, query: str) -> tuple[str, str, list[Paper]]:
            async with gate:
                source = self._sources[source_name]
                results = await source.search(query, FetchParams(max_results=self._max_results))
                return source_name, query, results

        tasks = []
        for query in plan.queries:
            source = self._sources.get(query.source)
            if source is None:
                dropped["source_unavailable"] = dropped.get("source_unavailable", 0) + 1
                logger.warning(
                    "skipping query for an unconfigured source",
                    extra={"source": query.source, "query": query.q},
                )
                continue
            queries_used.append(f"{query.source}: {query.q}")
            tasks.append(run_query(query.source, query.q))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        collected: dict[str, int] = {}
        for result in results:
            if isinstance(result, BaseException):
                failures.append(str(result))
                continue
            source_name, _query, found = result
            collected[source_name] = collected.get(source_name, 0) + len(found)
            papers.extend(found)

        for source_name, count in collected.items():
            per_source_counts[source_name] = per_source_counts.get(source_name, 0) + count
        for source_name in self._sources:
            per_source_counts.setdefault(source_name, 0)

        if not papers and failures:
            raise SourceError("every query failed: " + "; ".join(failures))

        # Sources report what they had to drop while parsing; merge that ledger.
        for source in self._sources.values():
            source_dropped = getattr(source, "dropped", None)
            if source_dropped:
                for key, value in source_dropped.items():
                    dropped[key] = dropped.get(key, 0) + value

        return CandidateList(
            papers=papers,
            queries_used=queries_used,
            per_source_counts=per_source_counts,
            cache=self._cache.stats() or CacheStats(),
            dropped=dropped,
        )

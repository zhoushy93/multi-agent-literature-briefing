"""Retriever tests: merging, degradation, and the cross-source fallback."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from briefing.agents.retriever import Retriever, to_plain_query
from briefing.errors import SourceError
from briefing.schemas import Paper, Query, SearchPlan
from briefing.sources.base import FetchParams
from briefing.sources.cache import CacheStore

RETRIEVED_AT = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)


def make_paper(identifier: str, origin: str) -> Paper:
    return Paper(
        paper_id=f"{origin}:{identifier}",
        title=f"Study {identifier}",
        authors=["A. Author"],
        year=2026,
        venue="arXiv",
        origin=origin,
        url=f"https://example.org/{identifier}",
        abstract="We study diffusion models for weather forecasting.",
        retrieved_at=RETRIEVED_AT,
    )


class FakeSource:
    def __init__(
        self,
        name: str,
        papers: list[Paper] | None = None,
        *,
        fail: bool = False,
        rate_limited: bool = False,
    ):
        self.name = name
        self._papers = papers or []
        self._fail = fail
        self.rate_limited = rate_limited
        self.queries: list[str] = []
        self.dropped: dict[str, int] = {}

    async def search(self, query: str, params: FetchParams) -> list[Paper]:
        self.queries.append(query)
        if self._fail:
            raise SourceError(f"{self.name} is unavailable")
        return list(self._papers)


def make_plan(*sources: str) -> SearchPlan:
    """A plan needs at least two queries, so repeat the last source if needed."""
    while len(sources) < 2:
        sources = (*sources, sources[-1] if sources else "arxiv")
    return SearchPlan(
        normalized_topic="diffusion models for weather forecasting",
        queries=[
            Query(source=name, q=f'all:"topic {index}" AND all:weather', rationale="r")
            for index, name in enumerate(sources, start=1)
        ],
        keywords=["a", "b", "c"],
        inclusion_criteria=["i"],
        exclusion_criteria=[],
        time_window=(None, None),
    )


def make_retriever(tmp_path: Path, sources: list[FakeSource], **kwargs: object) -> Retriever:
    params: dict[str, object] = {
        "sources": sources,
        "cache": CacheStore(root=tmp_path / "cache", enabled=False),
    }
    params.update(kwargs)
    return Retriever(**params)  # type: ignore[arg-type]


# --- query translation --------------------------------------------------------


def test_plain_query_drops_field_prefixes_and_operators() -> None:
    assert to_plain_query('all:"diffusion model" AND all:"climate modeling"') == (
        "diffusion model climate modeling"
    )


def test_plain_query_handles_parentheses_and_bare_terms() -> None:
    assert to_plain_query("(abs:nowcasting OR ti:radar) AND weather") == (
        "nowcasting radar weather"
    )


def test_plain_query_of_nothing_is_empty() -> None:
    assert to_plain_query('all:""') == ""


# --- retrieval ----------------------------------------------------------------


async def test_papers_are_merged_across_queries(tmp_path: Path) -> None:
    arxiv = FakeSource("arxiv", [make_paper("1", "arxiv")])
    retriever = make_retriever(tmp_path, [arxiv])
    candidates = await retriever.run(make_plan("arxiv", "arxiv"))

    assert len(candidates.papers) == 2, "the same query asked twice returns twice"
    assert candidates.per_source_counts == {"arxiv": 2}
    assert candidates.queries_used == [
        'arxiv: all:"topic 1" AND all:weather',
        'arxiv: all:"topic 2" AND all:weather',
    ]


async def test_an_unconfigured_source_is_recorded_not_fatal(tmp_path: Path) -> None:
    retriever = make_retriever(tmp_path, [FakeSource("arxiv", [make_paper("1", "arxiv")])])
    candidates = await retriever.run(make_plan("arxiv", "crossref"))

    assert candidates.dropped == {"source_unavailable": 1}
    assert len(candidates.papers) == 1


async def test_every_query_failing_is_a_source_error(tmp_path: Path) -> None:
    retriever = make_retriever(tmp_path, [FakeSource("arxiv", fail=True)], allow_fallback=False)
    with pytest.raises(SourceError, match="every query failed"):
        await retriever.run(make_plan("arxiv"))


async def test_fallback_answers_when_the_primary_is_rate_limited(tmp_path: Path) -> None:
    """arXiv returning 406 must degrade to OpenAlex, not fail the run."""
    arxiv = FakeSource("arxiv", fail=True)
    openalex = FakeSource("openalex", [make_paper("W1", "openalex")])
    retriever = make_retriever(tmp_path, [arxiv, openalex])

    candidates = await retriever.run(make_plan("arxiv"))

    # Both failed queries are re-asked; collapsing repeats is the normalizer's job.
    assert len(candidates.papers) == 2
    assert {paper.origin for paper in candidates.papers} == {"openalex"}
    assert candidates.per_source_counts == {"arxiv": 0, "openalex": 2}
    assert candidates.dropped["source_fallback"] == 2
    assert any("(fallback)" in query for query in candidates.queries_used)
    assert openalex.queries == ["topic 1 weather", "topic 2 weather"], "plain text"


async def test_fallback_is_skipped_when_disabled(tmp_path: Path) -> None:
    retriever = make_retriever(
        tmp_path,
        [FakeSource("arxiv", fail=True), FakeSource("openalex", [make_paper("W1", "openalex")])],
        allow_fallback=False,
    )
    with pytest.raises(SourceError):
        await retriever.run(make_plan("arxiv"))


async def test_a_working_primary_does_not_use_the_fallback(tmp_path: Path) -> None:
    arxiv = FakeSource("arxiv", [make_paper("1", "arxiv")])
    openalex = FakeSource("openalex", [make_paper("W1", "openalex")])
    retriever = make_retriever(tmp_path, [arxiv, openalex])

    candidates = await retriever.run(make_plan("arxiv"))

    assert openalex.queries == []
    assert "source_fallback" not in candidates.dropped


async def test_a_rate_limited_source_is_recorded(tmp_path: Path) -> None:
    arxiv = FakeSource("arxiv", fail=True, rate_limited=True)
    openalex = FakeSource("openalex", [make_paper("W1", "openalex")])
    retriever = make_retriever(tmp_path, [arxiv, openalex])

    candidates = await retriever.run(make_plan("arxiv"))

    assert candidates.dropped["source_rate_limited"] == 1
    assert len(candidates.papers) == 2

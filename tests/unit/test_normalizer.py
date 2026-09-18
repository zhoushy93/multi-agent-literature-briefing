"""Normalizer tests: dedupe paths, the pre-filter ledger, and stable ordering."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from briefing.agents.normalizer import (
    MIN_ABSTRACT_CHARS,
    Normalizer,
    deduplicate,
    normalize,
    normalize_doi,
    normalize_title,
)
from briefing.schemas import CacheStats, CandidateList, Paper

RETRIEVED_AT = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)
LONG_ABSTRACT = "x" * (MIN_ABSTRACT_CHARS + 50)


def make_paper(paper_id: str = "arxiv:2401.01234", **overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": paper_id,
        "title": "Diffusion Models for Weather Forecasting",
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "arXiv",
        "origin": "arxiv",
        "url": f"https://arxiv.org/abs/{paper_id}",
        "arxiv_id": None,
        "doi": None,
        "abstract": LONG_ABSTRACT,
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_candidates(papers: list[Paper], **overrides: Any) -> CandidateList:
    data: dict[str, Any] = {
        "papers": papers,
        "queries_used": ["all:diffusion"],
        "per_source_counts": {"arxiv": len(papers)},
        "cache": CacheStats(),
        "dropped": {},
    }
    data.update(overrides)
    return CandidateList(**data)


# --- normalisation helpers ---------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1234/ABC.Def", "10.1234/abc.def"),
        ("https://doi.org/10.1234/Abc", "10.1234/abc"),
        ("doi:10.1234/abc", "10.1234/abc"),
        ("  10.1234/abc  ", "10.1234/abc"),
    ],
)
def test_doi_normalisation(raw: str, expected: str) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Diffusion Models for Weather Forecasting", "diffusion models for weather forecasting"),
        ("Diffusion Models: A Survey", "diffusion models"),
        ("Score-Based  Generative, Models!", "score based generative models"),
        ("  Spaced   Out  ", "spaced out"),
    ],
)
def test_title_normalisation(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


# --- dedupe paths ------------------------------------------------------------


def test_doi_match_removes_a_duplicate() -> None:
    first = make_paper(
        paper_id="arxiv:2401.00001",
        doi="10.1234/abc",
        title="First title",
        arxiv_id="2401.00001",
    )
    second = make_paper(
        paper_id="crossref:xyz",
        doi="https://doi.org/10.1234/ABC",
        title="A completely different title",
        origin="crossref",
        arxiv_id=None,
    )
    survivors, removed = deduplicate([first, second])
    assert removed == 1
    assert len(survivors) == 1
    assert survivors[0].paper_id == "arxiv:2401.00001"


def test_arxiv_id_match_ignores_the_version_suffix() -> None:
    first = make_paper(arxiv_id="2401.01234v1", paper_id="arxiv:2401.01234")
    second = make_paper(arxiv_id="2401.01234v3", paper_id="arxiv:2401.01234v3")
    survivors, removed = deduplicate([first, second])
    assert removed == 1
    assert len(survivors) == 1


def test_title_match_removes_a_duplicate() -> None:
    first = make_paper(title="Diffusion Models for Weather Forecasting")
    second = make_paper(
        paper_id="openalex:W1",
        title="diffusion models for weather forecasting: a replication",
        origin="openalex",
    )
    survivors, removed = deduplicate([first, second])
    assert removed == 1
    assert len(survivors) == 1


def test_same_title_with_conflicting_dois_is_not_merged() -> None:
    """The title is a fallback id: a stronger id disagreement wins."""
    first = make_paper(doi="10.1234/first")
    second = make_paper(paper_id="crossref:other", doi="10.1234/second", origin="crossref")
    survivors, removed = deduplicate([first, second])
    assert removed == 0
    assert len(survivors) == 2


def test_records_merge_transitively_across_different_identifiers() -> None:
    """A record bridging two groups must fuse them; first-match matching would not."""
    first = make_paper(paper_id="arxiv:a", doi="10.1234/abc", title="Alpha Study")
    second = make_paper(paper_id="arxiv:b", title="Beta Study", arxiv_id="2401.00002")
    bridge = make_paper(
        paper_id="crossref:c",
        doi="10.1234/abc",
        title="Gamma Study",
        arxiv_id="2401.00002",
        origin="crossref",
    )
    survivors, removed = deduplicate([first, second, bridge])
    assert removed == 2
    assert len(survivors) == 1
    assert survivors[0].paper_id == "crossref:c"


def test_richest_record_survives() -> None:
    sparse = make_paper(paper_id="arxiv:1", title="Same Title", abstract="short", arxiv_id=None)
    rich = make_paper(
        paper_id="arxiv:2",
        title="same title",
        abstract=LONG_ABSTRACT,
        doi="10.1234/rich",
    )
    survivors, _ = deduplicate([sparse, rich])
    assert survivors[0].paper_id == "arxiv:2"
    assert survivors[0].doi == "10.1234/rich"


def test_no_duplicates_reports_zero_removed() -> None:
    papers = [
        make_paper(paper_id="arxiv:1", title="Alpha", arxiv_id="1"),
        make_paper(paper_id="arxiv:2", title="Beta", arxiv_id="2"),
    ]
    survivors, removed = deduplicate(papers)
    assert len(survivors) == 2
    assert removed == 0


def test_empty_input_yields_empty_output() -> None:
    survivors, removed = deduplicate([])
    assert survivors == []
    assert removed == 0


# --- ordering ----------------------------------------------------------------


def test_output_order_is_stable_across_input_permutations() -> None:
    papers = [
        make_paper(paper_id="arxiv:3", title="Gamma", arxiv_id="3", year=2021),
        make_paper(paper_id="arxiv:1", title="Alpha", arxiv_id="1", year=2024),
        make_paper(paper_id="arxiv:2", title="Beta", arxiv_id="2", year=2024),
    ]
    forward = [paper.paper_id for paper in normalize(make_candidates(papers)).papers]
    backward = [
        paper.paper_id for paper in normalize(make_candidates(list(reversed(papers)))).papers
    ]
    assert forward == backward == ["arxiv:1", "arxiv:2", "arxiv:3"]


def test_output_order_follows_source_then_year_then_id() -> None:
    papers = [
        make_paper(paper_id="openalex:1", title="Old Arxiv", origin="openalex", year=2024),
        make_paper(paper_id="arxiv:9", title="Old", origin="arxiv", year=2019),
        make_paper(paper_id="arxiv:2", title="New", origin="arxiv", year=2024),
        make_paper(paper_id="weird:1", title="Unknown Source", origin="blog", year=2025),
    ]
    ordered = [paper.paper_id for paper in normalize(make_candidates(papers)).papers]
    assert ordered == ["arxiv:2", "arxiv:9", "openalex:1", "weird:1"]


def test_repeated_runs_are_identical() -> None:
    papers = [make_paper(paper_id=f"arxiv:{i}", title=f"T{i}", arxiv_id=str(i)) for i in range(5)]
    first = normalize(make_candidates(papers))
    second = normalize(make_candidates(papers))
    assert first == second


# --- pre-filter ledger -------------------------------------------------------


def test_short_abstract_is_counted_but_kept() -> None:
    paper = make_paper(abstract="too short")
    result = normalize(make_candidates([paper]))
    assert result.dropped == {"short_abstract": 1}
    assert len(result.papers) == 1, "AGENTS.md §7 down-weights, it does not delete"


def test_missing_abstract_is_counted_but_kept() -> None:
    paper = make_paper(abstract="")
    result = normalize(make_candidates([paper]))
    assert result.dropped == {"no_abstract": 1}
    assert len(result.papers) == 1


def test_long_abstract_is_not_flagged() -> None:
    result = normalize(make_candidates([make_paper()]))
    assert result.dropped == {}


def test_ledger_accumulates_the_retrieval_stage_counts() -> None:
    papers = [make_paper(abstract="short"), make_paper(paper_id="arxiv:2", title="B", abstract="")]
    result = normalize(make_candidates(papers, dropped={"no_abstract": 4}))
    assert result.dropped == {"no_abstract": 5, "short_abstract": 1}


def test_the_pool_is_capped_for_the_screener() -> None:
    """AGENTS.md §7: the screener sees 20-40 candidates, not every candidate."""
    papers = [
        make_paper(paper_id=f"arxiv:{index}", title=f"T{index}", arxiv_id=str(index))
        for index in range(1, 13)
    ]
    result = normalize(make_candidates(papers), max_candidates=10)
    assert len(result.papers) == 10
    assert result.dropped == {"pool_capped": 2}


def test_a_pool_below_the_cap_is_untouched() -> None:
    papers = [
        make_paper(paper_id=f"arxiv:{index}", title=f"T{index}", arxiv_id=str(index))
        for index in range(1, 4)
    ]
    result = normalize(make_candidates(papers), max_candidates=10)
    assert len(result.papers) == 3
    assert result.dropped == {}


def test_duplicate_count_excludes_prefilter_dispositions() -> None:
    papers = [
        make_paper(arxiv_id="1", paper_id="arxiv:1"),
        make_paper(arxiv_id="1", paper_id="arxiv:1", abstract="short"),
        make_paper(paper_id="arxiv:2", title="Other", arxiv_id="2", abstract="tiny"),
    ]
    result = normalize(make_candidates(papers))
    assert result.duplicates_removed == 1
    assert len(result.papers) == 2
    # The merged-away record's short abstract is not double counted.
    assert result.dropped == {"short_abstract": 1}


# --- agent wrapper -----------------------------------------------------------


async def test_agent_run_matches_the_pure_function() -> None:
    candidates = make_candidates([make_paper()])
    assert await Normalizer().run(candidates) == normalize(candidates)

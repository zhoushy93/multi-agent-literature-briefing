"""Screener tests. The model is a stub, so nothing leaves the machine."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from briefing.agents.normalizer import MIN_ABSTRACT_CHARS, normalize
from briefing.agents.screener import PROMPT_NAME, Screener
from briefing.errors import InsufficientPapersError, SchemaValidationError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.schemas import (
    CacheStats,
    CandidateList,
    DedupedCandidates,
    Paper,
    Query,
    SearchPlan,
)

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
LONG_ABSTRACT = "y" * (MIN_ABSTRACT_CHARS + 20)


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the screener called the model more often than expected")
        return LLMResponse(text=self._replies.pop(0), prompt_tokens=1, completion_tokens=1)


def make_paper(index: int, **overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": f"arxiv:2401.0000{index}",
        "title": f"Study {index}",
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "arXiv",
        "origin": "arxiv",
        "url": f"https://arxiv.org/abs/2401.0000{index}",
        "arxiv_id": f"2401.0000{index}",
        "abstract": LONG_ABSTRACT,
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_candidates(count: int = 3, **overrides: Any) -> DedupedCandidates:
    data: dict[str, Any] = {
        "papers": [make_paper(index) for index in range(1, count + 1)],
        "duplicates_removed": 0,
        "dropped": {},
        "queries_used": ['arxiv: all:"diffusion model"'],
    }
    data.update(overrides)
    return DedupedCandidates(**data)


def make_plan(**overrides: Any) -> SearchPlan:
    data: dict[str, Any] = {
        "normalized_topic": "diffusion models for weather forecasting",
        "queries": [
            Query(source="arxiv", q="all:diffusion", rationale="core term"),
            Query(source="arxiv", q="all:forecasting", rationale="second term"),
        ],
        "keywords": ["diffusion", "forecasting", "generative"],
        "inclusion_criteria": ["proposes a generative forecasting model"],
        "exclusion_criteria": ["is not peer reviewed"],
        "time_window": (None, None),
    }
    data.update(overrides)
    return SearchPlan(**data)


def choice(tag: str, rank: int = 1, score: float = 0.9, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tag": tag,
        "rank": rank,
        "relevance_score": score,
        "rationale": f"{tag} matches the inclusion criteria",
        "matched_inclusion": ["proposes a generative forecasting model"],
        "matched_exclusion": [],
    }
    data.update(overrides)
    return data


def payload(choices: list[dict[str, Any]], notes: str | None = None) -> str:
    return json.dumps({"selected": choices, "selection_notes": notes})


async def run_screener(
    replies: list[str],
    candidates: DedupedCandidates | None = None,
    **kwargs: Any,
) -> tuple[Any, Screener, FakeLLM]:
    llm = FakeLLM(replies)
    screener = Screener(llm=llm, **kwargs)
    result = await screener.run(candidates or make_candidates(), make_plan())
    return result, screener, llm


# --- happy path --------------------------------------------------------------


async def test_selects_and_maps_tags_back_to_papers() -> None:
    selected, _, _ = await run_screener(
        [payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])]
    )
    assert [item.paper.paper_id for item in selected.items] == [
        "arxiv:2401.00001",
        "arxiv:2401.00002",
        "arxiv:2401.00003",
    ]
    assert [item.rank for item in selected.items] == [1, 2, 3]


async def test_ranks_are_renumbered_contiguously() -> None:
    """The schema promises 1-based contiguous ranks; the model may send any."""
    selected, _, _ = await run_screener(
        [payload([choice("C3", 9), choice("C1", 5), choice("C2", 2)])]
    )
    assert [item.paper.paper_id for item in selected.items] == [
        "arxiv:2401.00002",
        "arxiv:2401.00001",
        "arxiv:2401.00003",
    ]
    assert [item.rank for item in selected.items] == [1, 2, 3]


async def test_rank_ties_keep_the_model_order() -> None:
    selected, _, _ = await run_screener(
        [payload([choice("C2", 1), choice("C1", 1), choice("C3", 2)])]
    )
    assert [item.paper.paper_id for item in selected.items][:2] == [
        "arxiv:2401.00002",
        "arxiv:2401.00001",
    ]


async def test_selection_notes_are_passed_through() -> None:
    choices = [choice(f"C{index}", index) for index in (1, 2, 3)]
    selected, _, _ = await run_screener([payload(choices, notes="All three are preprints.")])
    assert selected.selection_notes == "All three are preprints."


async def test_scores_are_carried_over() -> None:
    choices = [choice(f"C{index}", index, score=0.5) for index in (1, 2, 3)]
    selected, _, _ = await run_screener([payload(choices)])
    assert all(item.relevance_score == 0.5 for item in selected.items)


# --- tags --------------------------------------------------------------------


async def test_out_of_candidate_tag_is_dropped_with_a_warning() -> None:
    selected, screener, _ = await run_screener(
        [payload([choice("C1", 1), choice("C2", 2), choice("C99", 3), choice("C3", 4)])]
    )
    assert len(selected.items) == 3
    assert [warning.kind for warning in screener.warnings] == ["unknown_candidate"]
    assert "C99" in screener.warnings[0].detail


async def test_duplicate_tag_is_dropped_with_a_warning() -> None:
    selected, screener, _ = await run_screener(
        [payload([choice("C1", 1), choice("C1", 2), choice("C2", 3), choice("C3", 4)])]
    )
    assert len(selected.items) == 3
    assert [warning.kind for warning in screener.warnings] == ["duplicate_reference"]


async def test_unknown_tag_lookalike_is_not_resolved_fuzzily() -> None:
    """'c1' or 'C1 ' must not silently match C1."""
    with pytest.raises(InsufficientPapersError):
        await run_screener([payload([choice("c1", 1), choice("C1 ", 2)])])


# --- insufficient papers -----------------------------------------------------


async def test_insufficient_papers_raises_with_the_search_context() -> None:
    llm = FakeLLM([payload([choice("C1", 1), choice("C2", 2)])])
    screener = Screener(llm=llm)
    with pytest.raises(InsufficientPapersError) as excinfo:
        await screener.run(make_candidates(count=2), make_plan())

    error = excinfo.value
    assert (error.found, error.required, error.candidate_count) == (2, 3, 2)
    assert error.suggestions, "the error must tell the caller how to widen the search"
    message = str(error)
    assert "2 usable paper" in message
    assert "candidates offered: 2" in message
    assert "widen the topic" in message


async def test_empty_candidate_list_fails_without_calling_the_model() -> None:
    llm = FakeLLM([])
    screener = Screener(llm=llm)
    with pytest.raises(InsufficientPapersError) as excinfo:
        await screener.run(make_candidates(count=0), make_plan())
    assert excinfo.value.found == 0
    assert llm.requests == [], "no point paying for a screening call with nothing to screen"


async def test_out_of_candidate_tags_can_drop_below_the_minimum() -> None:
    llm = FakeLLM([payload([choice("C1", 1), choice("C42", 2), choice("C43", 3)])])
    screener = Screener(llm=llm)
    with pytest.raises(InsufficientPapersError) as excinfo:
        await screener.run(make_candidates(count=3), make_plan())
    assert excinfo.value.found == 1
    assert excinfo.value.candidate_count == 3
    assert [warning.kind for warning in screener.warnings] == [
        "unknown_candidate",
        "unknown_candidate",
    ]


# --- truncation --------------------------------------------------------------


async def test_truncate_to_the_maximum_and_record_a_warning() -> None:
    choices = [choice(f"C{index}", index) for index in range(1, 6)]
    selected, screener, _ = await run_screener(
        [payload(choices)],
        make_candidates(count=5),
        max_papers=3,
    )
    assert len(selected.items) == 3
    assert [item.rank for item in selected.items] == [1, 2, 3]
    assert [warning.kind for warning in screener.warnings] == ["length_limit"]


async def test_truncation_keeps_the_top_ranks() -> None:
    choices = [
        choice("C4", 1),
        choice("C5", 2),
        choice("C1", 3),
        choice("C2", 4),
        choice("C3", 5),
    ]
    selected, _, _ = await run_screener(
        [payload(choices)],
        make_candidates(count=5),
        max_papers=3,
    )
    assert [item.paper.paper_id for item in selected.items] == [
        "arxiv:2401.00004",
        "arxiv:2401.00005",
        "arxiv:2401.00001",
    ]


# --- prompt construction -----------------------------------------------------


async def test_prompt_receives_topic_criteria_and_bounds() -> None:
    _, _, llm = await run_screener([payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])])
    user = llm.requests[0].user
    assert "diffusion models for weather forecasting" in user
    assert "include: proposes a generative forecasting model" in user
    assert "exclude: is not peer reviewed" in user
    assert "between 3 and 5" in user
    assert "C1:" in user and "C3:" in user


async def test_short_abstract_candidates_are_marked_in_the_prompt() -> None:
    candidates = DedupedCandidates(
        papers=[make_paper(1, abstract="tiny"), make_paper(2), make_paper(3)],
        duplicates_removed=0,
        dropped={"short_abstract": 1},
    )
    _, _, llm = await run_screener(
        [payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])],
        candidates,
    )
    lines = llm.requests[0].user.splitlines()
    marked = [line for line in lines if "[short-abstract]" in line]
    assert len(marked) == 1
    assert marked[0].startswith("C1")


async def test_marking_agrees_with_the_normalizer_ledger() -> None:
    """The down-weight signal must line up with what S3 recorded."""
    candidates = normalize(
        CandidateList(
            papers=[make_paper(1, abstract="tiny"), make_paper(2), make_paper(3, abstract="")],
            queries_used=["all:diffusion"],
            per_source_counts={"arxiv": 3},
            cache=CacheStats(),
            dropped={},
        )
    )
    assert candidates.dropped == {"no_abstract": 1, "short_abstract": 1}

    _, _, llm = await run_screener(
        [payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])],
        candidates,
    )
    marked = llm.requests[0].user.count("[short-abstract]")
    assert marked == candidates.dropped["no_abstract"] + candidates.dropped["short_abstract"]


async def test_request_records_stage_temperature_and_prompt_version() -> None:
    _, _, llm = await run_screener([payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])])
    request = llm.requests[0]
    assert request.stage == "S4"
    assert request.temperature == 0.0
    assert request.prompt_version == PROMPT_NAME


# --- model misbehaviour ------------------------------------------------------


async def test_empty_selection_is_rejected_by_the_schema() -> None:
    llm = FakeLLM([payload([])] * 3)
    with pytest.raises(SchemaValidationError):
        await Screener(llm=llm).run(make_candidates(), make_plan())
    assert len(llm.requests) == 3, "one attempt plus two schema retries"


async def test_out_of_range_score_is_rejected() -> None:
    bad = payload([choice("C1", 1, score=1.5)])
    llm = FakeLLM([bad] * 3)
    with pytest.raises(SchemaValidationError):
        await Screener(llm=llm).run(make_candidates(), make_plan())


async def test_restated_metadata_is_rejected() -> None:
    """The model must return a tag, not a paper object."""
    bad = json.dumps({"selected": [{"paper": {"title": "Invented"}}]})
    llm = FakeLLM([bad] * 3)
    with pytest.raises(SchemaValidationError):
        await Screener(llm=llm).run(make_candidates(), make_plan())


async def test_retry_feedback_reaches_the_model() -> None:
    good = payload([choice("C1", 1), choice("C2", 2), choice("C3", 3)])
    llm = FakeLLM([payload([]), good])
    selected = await Screener(llm=llm).run(make_candidates(), make_plan())
    assert len(selected.items) == 3
    assert "Validation errors" in llm.requests[1].user


@pytest.mark.parametrize(
    ("min_papers", "max_papers"),
    [(5, 3), (2, 5), (3, 6)],
)
def test_constructor_rejects_bounds_outside_the_contract(
    min_papers: int,
    max_papers: int,
) -> None:
    with pytest.raises(ValueError, match="min_papers"):
        Screener(llm=FakeLLM([]), min_papers=min_papers, max_papers=max_papers)

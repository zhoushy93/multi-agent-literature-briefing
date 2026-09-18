"""Analyzer tests: quote provenance, paper_id binding, and bounded fan-out."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest

from briefing.agents.analyzer import (
    DEFAULT_MAX_INPUT_CHARS,
    PROMPT_NAME,
    Analyzer,
    analyze_all,
    shorten_quote,
)
from briefing.errors import AnalysisFailedError, SchemaValidationError
from briefing.evidence import build_haystack, quote_is_supported
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.schemas import MAX_QUOTE_CHARS, Paper, ScreenedPaper

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
ABSTRACT = "We study diffusion models for weather forecasting on ERA5 reanalysis."
PAPER_ID = "arxiv:2401.00001"


def make_paper(**overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": PAPER_ID,
        "title": "Diffusion Models for Weather Forecasting",
        "authors": ["Alice Author"],
        "year": 2024,
        "venue": "Journal of Weather ML",
        "origin": "arxiv",
        "url": "https://arxiv.org/abs/2401.00001",
        "arxiv_id": "2401.00001",
        "abstract": ABSTRACT,
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_screened(*, rank: int = 1, **overrides: Any) -> ScreenedPaper:
    return ScreenedPaper(
        paper=make_paper(**overrides),
        rank=rank,
        relevance_score=0.9,
        rationale="relevant",
    )


def analysis_payload(
    paper_id: str = PAPER_ID,
    quotes: list[str] | None = None,
    **overrides: Any,
) -> str:
    payload: dict[str, Any] = {
        "paper_id": paper_id,
        "problem": "Forecasting degrades at long horizons.",
        "method": "Conditional diffusion with a residual backbone.",
        "data_and_experiments": "ERA5 reanalysis, 1979-2020.",
        "key_findings": ["Lower RMSE at day 7."],
        "limitations": ["Evaluated on one region."],
        "relevance": "Relevant because it is the topic's core method.",
        "reusable_ideas": ["Noise schedule annealing"],
        "evidence": [
            {
                "field": "abstract",
                "locator": f"abstract[{index * 20}:{index * 20 + 20}]",
                "quote": quote,
            }
            for index, quote in enumerate(quotes or ["We study diffusion models"])
        ],
        "confidence": 0.7,
    }
    payload.update(overrides)
    return json.dumps(payload)


class FakeLLM:
    """Returns canned replies in order for a single paper."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the analyzer called the model more often than expected")
        return LLMResponse(text=self._replies.pop(0), prompt_tokens=1, completion_tokens=1)


PAPER_ID_PATTERN = re.compile(r"paper_id:\s*(\S+)")


class RoutingLLM:
    """Answers per paper_id and tracks how many calls are in flight at once."""

    def __init__(self, script: dict[str, list[str]], *, delay: float = 0.0) -> None:
        self._script = {key: list(values) for key, values in script.items()}
        self._delay = delay
        self.active = 0
        self.peak = 0
        self.calls: list[str] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        match = PAPER_ID_PATTERN.search(request.user)
        assert match is not None, "the prompt must carry the paper_id"
        paper_id = match.group(1)
        self.calls.append(paper_id)

        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            if self._delay:
                await asyncio.sleep(self._delay)
            replies = self._script[paper_id]
            if not replies:
                raise AssertionError(f"unexpected extra call for {paper_id}")
            return LLMResponse(text=replies.pop(0), prompt_tokens=1, completion_tokens=1)
        finally:
            self.active -= 1


# --- happy path --------------------------------------------------------------


async def test_returns_the_analysis_for_the_paper() -> None:
    llm = FakeLLM([analysis_payload()])
    analysis = await Analyzer(llm=llm).run(make_screened())
    assert analysis.paper_id == PAPER_ID
    assert analysis.key_findings == ["Lower RMSE at day 7."]
    assert analysis.confidence == 0.7


async def test_request_records_stage_temperature_and_prompt_version() -> None:
    llm = FakeLLM([analysis_payload()])
    await Analyzer(llm=llm).run(make_screened())
    request = llm.requests[0]
    assert request.stage == "S5"
    assert request.temperature == 0.2
    assert request.prompt_version == PROMPT_NAME


async def test_prompt_carries_the_paper_material() -> None:
    llm = FakeLLM([analysis_payload()])
    await Analyzer(llm=llm).run(make_screened())
    user = llm.requests[0].user
    assert PAPER_ID in user
    assert "Diffusion Models for Weather Forecasting" in user
    assert ABSTRACT in user
    assert "Journal of Weather ML" in user


async def test_prompt_carries_the_topic_so_relevance_can_be_judged() -> None:
    llm = FakeLLM([analysis_payload()])
    analyzer = Analyzer(llm=llm, topic="diffusion models for weather forecasting", lang="en")
    await analyzer.run(make_screened())
    user = llm.requests[0].user
    assert "diffusion models for weather forecasting" in user
    assert "relevance" in user


async def test_relevance_is_required_by_the_contract() -> None:
    payload = json.loads(analysis_payload())
    payload.pop("relevance")
    llm = FakeLLM([json.dumps(payload)] * 3)
    with pytest.raises(SchemaValidationError):
        await Analyzer(llm=llm).run(make_screened())


# --- paper_id binding --------------------------------------------------------


async def test_paper_id_mismatch_fails_immediately() -> None:
    """A wrong id would attach an analysis to the wrong paper."""
    llm = FakeLLM([analysis_payload(paper_id="arxiv:9999.99999")])
    with pytest.raises(AnalysisFailedError) as excinfo:
        await Analyzer(llm=llm).run(make_screened())
    assert excinfo.value.paper_ids == [PAPER_ID]
    assert "different paper" in excinfo.value.reason
    assert len(llm.requests) == 1, "a mismatch is not retried"


# --- quote provenance --------------------------------------------------------


@pytest.mark.parametrize(
    ("quote", "supported"),
    [
        ("We study diffusion models", True),
        ("we study DIFFUSION models", True),
        ("We   study\n diffusion   models", True),
        ("Diffusion Models for Weather Forecasting", True),
        ("Journal of Weather ML", True),
        ("We study diffusion", True),
        ("We study quantum computing", False),
        ("", False),
        ("Alice Author, 2024", False),  # a stitched quote is not verbatim
    ],
)
def test_quote_is_supported(quote: str, supported: bool) -> None:
    assert quote_is_supported(quote, build_haystack(make_paper())) is supported


def test_ellipsis_inside_a_quote_is_tolerated() -> None:
    haystack = build_haystack(make_paper())
    assert quote_is_supported("We study diffusion ... on ERA5", haystack)
    assert quote_is_supported("We study diffusion … on ERA5", haystack)


def test_ellipsis_fragments_must_stay_in_order() -> None:
    assert not quote_is_supported("on ERA5 ... We study", build_haystack(make_paper()))


async def test_invented_quote_triggers_a_retry_and_then_succeeds() -> None:
    llm = FakeLLM(
        [
            analysis_payload(quotes=["We study quantum computing"]),
            analysis_payload(quotes=["We study diffusion models"]),
        ]
    )
    analysis = await Analyzer(llm=llm).run(make_screened())
    assert analysis.evidence[0].quote == "We study diffusion models"
    assert len(llm.requests) == 2
    assert "do not appear" in llm.requests[1].user


async def test_persistently_invented_quote_fails_after_the_retry_budget() -> None:
    llm = FakeLLM([analysis_payload(quotes=["Invented sentence"])] * 3)
    with pytest.raises(SchemaValidationError):
        await Analyzer(llm=llm).run(make_screened())
    assert len(llm.requests) == 3, "one attempt plus two retries"


async def test_partially_bad_evidence_is_not_silently_kept() -> None:
    """One bad quote is enough to reject the whole reply."""
    llm = FakeLLM(
        [
            analysis_payload(quotes=["We study diffusion", "Totally made up"]),
            analysis_payload(quotes=["We study diffusion", "forecasting on ERA5"]),
        ]
    )
    analysis = await Analyzer(llm=llm).run(make_screened())
    assert [item.quote for item in analysis.evidence] == [
        "We study diffusion",
        "forecasting on ERA5",
    ]
    assert len(llm.requests) == 2


async def test_empty_evidence_is_rejected() -> None:
    llm = FakeLLM([analysis_payload(evidence=[])] * 3)
    with pytest.raises(SchemaValidationError):
        await Analyzer(llm=llm).run(make_screened())


LONG_ABSTRACT = "We study diffusion models for weather forecasting on ERA5 reanalysis. " * 10


def test_shorten_quote_keeps_a_verbatim_prefix() -> None:
    long_quote = LONG_ABSTRACT.strip()
    shortened = shorten_quote(long_quote)
    assert len(shortened) <= MAX_QUOTE_CHARS
    assert shortened.endswith("…")
    assert long_quote.startswith(shortened.removesuffix(" …"))


def test_short_quotes_are_left_alone() -> None:
    assert shorten_quote("We study diffusion") == "We study diffusion"


async def test_over_long_quotes_are_trimmed_instead_of_failing_the_paper() -> None:
    """A formatting overflow must not cost a whole run."""
    paper = make_screened(abstract=LONG_ABSTRACT.strip())
    llm = FakeLLM([analysis_payload(quotes=[LONG_ABSTRACT.strip()])])
    analyzer = Analyzer(llm=llm)

    analysis = await analyzer.run(paper)

    assert len(llm.requests) == 1, "the quote is trimmed, not retried"
    quote = analysis.evidence[0].quote
    assert len(quote) <= MAX_QUOTE_CHARS
    assert quote_is_supported(quote, build_haystack(paper.paper))
    assert analyzer.shortened_quotes == 1


# --- input budget ------------------------------------------------------------


async def test_long_abstract_is_truncated_to_the_input_budget() -> None:
    llm = FakeLLM([analysis_payload()])
    analyzer = Analyzer(llm=llm)
    await analyzer.run(make_screened(abstract=ABSTRACT + " " + "padding " * 3000))
    user = llm.requests[0].user
    assert len(user) <= DEFAULT_MAX_INPUT_CHARS
    assert "...[truncated]" in user
    assert analyzer.truncations == 1


async def test_short_input_is_not_marked_as_truncated() -> None:
    llm = FakeLLM([analysis_payload()])
    analyzer = Analyzer(llm=llm)
    await analyzer.run(make_screened())
    assert analyzer.truncations == 0
    assert "...[truncated]" not in llm.requests[0].user


# --- fan-out -----------------------------------------------------------------


def batch(count: int) -> list[ScreenedPaper]:
    return [
        make_screened(
            rank=index,
            paper_id=f"arxiv:2401.0000{index}",
            arxiv_id=f"2401.0000{index}",
            title=f"Study {index}",
        )
        for index in range(1, count + 1)
    ]


def script_for(papers: list[ScreenedPaper]) -> dict[str, list[str]]:
    return {
        screened.paper.paper_id: [analysis_payload(paper_id=screened.paper.paper_id)]
        for screened in papers
    }


async def test_concurrency_is_capped_by_the_semaphore() -> None:
    papers = batch(6)
    llm = RoutingLLM(script_for(papers), delay=0.01)
    await analyze_all(Analyzer(llm=llm), papers, max_concurrency=2)
    assert llm.peak == 2, "the semaphore must allow parallelism, but only this much"
    assert len(llm.calls) == 6


async def test_injected_semaphore_is_used() -> None:
    papers = batch(4)
    llm = RoutingLLM(script_for(papers), delay=0.01)
    await analyze_all(Analyzer(llm=llm), papers, semaphore=asyncio.Semaphore(1))
    assert llm.peak == 1


async def test_batch_preserves_the_input_order() -> None:
    papers = batch(4)
    llm = RoutingLLM(script_for(papers), delay=0.005)
    analyses = await analyze_all(Analyzer(llm=llm), papers, max_concurrency=4)
    assert [analysis.paper_id for analysis in analyses] == [
        screened.paper.paper_id for screened in papers
    ]


async def test_empty_batch_returns_no_analyses() -> None:
    assert await analyze_all(Analyzer(llm=FakeLLM([])), []) == []


async def test_one_failing_paper_fails_the_whole_batch() -> None:
    papers = batch(3)
    script = script_for(papers)
    script["arxiv:2401.00002"] = [
        analysis_payload(paper_id="arxiv:2401.00002", quotes=["nope"])
    ] * 3
    llm = RoutingLLM(script)
    with pytest.raises(AnalysisFailedError) as excinfo:
        await analyze_all(Analyzer(llm=llm), papers)
    assert excinfo.value.paper_ids == ["arxiv:2401.00002"]
    assert "1 of 3" in excinfo.value.reason


async def test_batch_reports_every_failed_paper() -> None:
    papers = batch(3)
    script = script_for(papers)
    for paper_id in ("arxiv:2401.00001", "arxiv:2401.00003"):
        script[paper_id] = [analysis_payload(paper_id="wrong", quotes=["nope"])]
    llm = RoutingLLM(script)
    with pytest.raises(AnalysisFailedError) as excinfo:
        await analyze_all(Analyzer(llm=llm), papers)
    assert excinfo.value.paper_ids == ["arxiv:2401.00001", "arxiv:2401.00003"]

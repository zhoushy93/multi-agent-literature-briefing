"""Synthesizer tests: closed citation-key set, revision mode, and layering."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from briefing.agents.synthesizer import (
    PROMPT_NAME,
    Synthesizer,
    citation_key,
    detect_language,
    render_violations,
)
from briefing.errors import BriefingError, SchemaValidationError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.schemas import (
    MAX_SUMMARY_CHARS,
    Paper,
    PaperAnalysis,
    ScreenedPaper,
    SelectedPapers,
    TopicRequest,
    Violation,
)

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the synthesizer called the model more often than expected")
        return LLMResponse(text=self._replies.pop(0), prompt_tokens=1, completion_tokens=1)


def make_paper(index: int, **overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": f"arxiv:2401.0000{index}",
        "title": f"Diffusion Study {index}",
        "authors": ["Alice Author"],
        "year": 2024,
        "venue": "Journal of Weather ML",
        "origin": "arxiv",
        "url": f"https://arxiv.org/abs/2401.0000{index}",
        "arxiv_id": f"2401.0000{index}",
        "abstract": "We study diffusion models for weather forecasting.",
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_selected(count: int = 3) -> SelectedPapers:
    return SelectedPapers(
        items=[
            ScreenedPaper(
                paper=make_paper(index),
                rank=index,
                relevance_score=0.9,
                rationale="relevant",
            )
            for index in range(1, count + 1)
        ]
    )


def make_analyses(count: int = 3) -> list[PaperAnalysis]:
    return [
        PaperAnalysis(
            paper_id=f"arxiv:2401.0000{index}",
            problem=f"Problem {index}",
            method=f"Method {index}",
            data_and_experiments=f"Dataset {index}",
            key_findings=[f"Finding {index}"],
            limitations=[f"Limitation {index}"],
            relevance=f"Why paper {index} matters for the topic.",
            reusable_ideas=[f"Idea {index}"],
            evidence=[
                {
                    "field": "abstract",
                    "locator": "abstract[0:40]",
                    "quote": "We study diffusion models",
                }
            ],
            confidence=0.6,
        )
        for index in range(1, count + 1)
    ]


def briefing_payload(keys: list[str] | None = None, **overrides: Any) -> str:
    """A draft: the model writes everything except the per-paper cards."""
    chosen = keys or ["P1", "P2", "P3"]
    payload: dict[str, Any] = {
        "run_id": "invented-by-the-model",
        "topic": "invented topic",
        "lang": "en",
        "executive_summary": "Diffusion models improve long-range forecasts.",
        "background_md": "## Background",
        "method_md": "## Method and criteria",
        "comparison": [
            {
                "citation_key": key,
                "task": "Forecasting",
                "method_family": "Diffusion",
                "data": "ERA5",
                "metrics": "RMSE",
                "main_result": "Lower error",
            }
            for key in chosen
        ],
        "gaps_and_open_questions": [
            {"text": "Regional transfer is untested.", "citation_keys": [chosen[0]]}
        ],
        "further_reading": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


def make_request(**overrides: Any) -> TopicRequest:
    data: dict[str, Any] = {
        "topic": "diffusion models for weather forecasting",
        "out_dir": "/tmp/out",
        "run_id": "20260917T1200Z-diffusion",
    }
    data.update(overrides)
    return TopicRequest(**data)


async def run_synth(
    replies: list[str],
    *,
    selected: SelectedPapers | None = None,
    analyses: list[PaperAnalysis] | None = None,
    request: TopicRequest | None = None,
    violations: list[Violation] | None = None,
) -> tuple[Any, FakeLLM]:
    llm = FakeLLM(replies)
    synthesizer = Synthesizer(llm=llm)
    briefing = await synthesizer.run(
        selected or make_selected(),
        analyses if analyses is not None else make_analyses(),
        request or make_request(),
        violations=violations or [],
    )
    return briefing, llm


# --- happy path --------------------------------------------------------------


async def test_produces_a_briefing() -> None:
    briefing, _ = await run_synth([briefing_payload()])
    assert len(briefing.paper_cards) == 3
    assert [card.citation_key for card in briefing.paper_cards] == ["P1", "P2", "P3"]
    assert len(briefing.comparison) == 3


async def test_the_cards_carry_the_verified_analyses_not_model_output() -> None:
    """The model cannot flatten or alter an analysis it never writes."""
    analyses = make_analyses()
    briefing, _ = await run_synth([briefing_payload()], analyses=analyses)
    assert [card.analysis for card in briefing.paper_cards] == analyses


async def test_run_id_topic_and_lang_are_authoritative() -> None:
    """The model echoes them, but the run's own values win."""
    briefing, _ = await run_synth([briefing_payload()])
    assert briefing.run_id == "20260917T1200Z-diffusion"
    assert briefing.topic == "diffusion models for weather forecasting"


async def test_chinese_topic_switches_the_report_language() -> None:
    request = make_request(topic="扩散模型用于天气预报")
    briefing, _ = await run_synth([briefing_payload(lang="en")], request=request)
    assert briefing.lang == "zh"


@pytest.mark.parametrize(
    ("topic", "expected"),
    [
        ("diffusion models for weather forecasting", "en"),
        ("扩散模型用于天气预报", "zh"),
        ("Diffusion 模型", "zh"),
        ("", "en"),
    ],
)
def test_detect_language(topic: str, expected: str) -> None:
    assert detect_language(topic) == expected


async def test_request_records_stage_temperature_and_prompt_version() -> None:
    _, llm = await run_synth([briefing_payload()])
    request = llm.requests[0]
    assert request.stage == "S6"
    assert request.temperature == 0.3
    assert request.prompt_version == PROMPT_NAME


# --- catalogue ---------------------------------------------------------------


async def test_catalogue_lists_every_key_in_selection_order() -> None:
    _, llm = await run_synth([briefing_payload()])
    user = llm.requests[0].user
    assert "Allowed citation keys: P1, P2, P3" in user
    assert user.index("[P1] Diffusion Study 1") < user.index("[P3] Diffusion Study 3")


async def test_catalogue_carries_each_analysis() -> None:
    _, llm = await run_synth([briefing_payload()])
    user = llm.requests[0].user
    for index in (1, 2, 3):
        assert f"Problem {index}" in user
        assert f"Method {index}" in user
        assert f"Finding {index}" in user


def test_citation_key_is_one_based() -> None:
    assert [citation_key(position) for position in (1, 2, 5)] == ["P1", "P2", "P5"]


# --- closed citation key set -------------------------------------------------


async def test_all_allowed_keys_are_accepted() -> None:
    briefing, _ = await run_synth([briefing_payload()])
    assert {card.citation_key for card in briefing.paper_cards} == {"P1", "P2", "P3"}


async def test_unknown_citation_key_triggers_a_retry() -> None:
    llm = FakeLLM([briefing_payload(keys=["P1", "P2", "P9"]), briefing_payload()])
    briefing = await Synthesizer(llm=llm).run(make_selected(), make_analyses(), make_request())
    assert len(briefing.paper_cards) == 3
    assert len(llm.requests) == 2
    assert "P9" in llm.requests[1].user


async def test_unknown_key_inside_a_claim_is_caught() -> None:
    bad = briefing_payload()
    payload = json.loads(bad)
    payload["further_reading"] = [{"text": "Read more.", "citation_keys": ["P7"]}]
    llm = FakeLLM([json.dumps(payload), bad])
    await Synthesizer(llm=llm).run(make_selected(), make_analyses(), make_request())
    assert "P7" in llm.requests[1].user


async def test_persistently_unknown_key_fails_after_the_retry_budget() -> None:
    llm = FakeLLM([briefing_payload(keys=["P1", "P2", "P99"])] * 3)
    with pytest.raises(SchemaValidationError):
        await Synthesizer(llm=llm).run(make_selected(), make_analyses(), make_request())
    assert len(llm.requests) == 3


# --- rules owned by the verifier ---------------------------------------------


async def test_summary_length_limit_is_requested_in_the_prompt() -> None:
    _, llm = await run_synth([briefing_payload()])
    user = llm.requests[0].user
    assert str(MAX_SUMMARY_CHARS) in user
    assert "at most" in user


async def test_over_long_summary_length_is_passed_through_to_the_verifier() -> None:
    """S6 asks for the limit; L1 reports a breach so the revision loop can run.

    If S6 rejected it here, the verifier's length_limit rule would never fire
    in a normal run and the revision path would be dead code.
    """
    over_long = "x" * (MAX_SUMMARY_CHARS + 40)
    briefing, _ = await run_synth([briefing_payload(executive_summary=over_long)])
    assert len(briefing.executive_summary) > MAX_SUMMARY_CHARS


async def test_comparison_rows_are_requested_in_the_prompt() -> None:
    _, llm = await run_synth([briefing_payload()])
    assert "exactly one comparison row for every paper" in llm.requests[0].user


async def test_comparison_rows_are_passed_through_to_the_verifier() -> None:
    """A row-count mismatch is L1's call, not the synthesizer's."""
    payload = json.loads(briefing_payload())
    payload["comparison"] = payload["comparison"][:1]
    briefing, _ = await run_synth([json.dumps(payload)])
    assert len(briefing.comparison) == 1
    assert len(briefing.paper_cards) == 3


# --- revision mode -----------------------------------------------------------


def test_first_draft_has_no_violations_block() -> None:
    assert render_violations([], ["P1", "P2"]) == (
        "There is no earlier draft. This is the first attempt."
    )


async def test_revision_mode_renders_the_violations() -> None:
    violations = [
        Violation(
            kind="length_limit",
            severity="fatal",
            location="executive_summary",
            detail=f"summary is {MAX_SUMMARY_CHARS + 48} characters, limit is {MAX_SUMMARY_CHARS}",
        ),
        Violation(
            kind="unknown_citation_key",
            severity="fatal",
            location="gaps_and_open_questions[1]",
            detail="key P9 does not exist",
        ),
    ]
    _, llm = await run_synth([briefing_payload()], violations=violations)
    user = llm.requests[0].user
    assert "A previous draft was rejected" in user
    assert "length_limit at executive_summary" in user
    assert "unknown_citation_key at gaps_and_open_questions[1]" in user
    assert "Every citation key must be one of: P1, P2, P3." in user


async def test_first_draft_prompt_says_so() -> None:
    _, llm = await run_synth([briefing_payload()])
    assert "no earlier draft" in llm.requests[0].user
    assert "rejected by the verifier" not in llm.requests[0].user


# --- input validation --------------------------------------------------------


async def test_missing_analysis_is_rejected_before_calling_the_model() -> None:
    llm = FakeLLM([briefing_payload()])
    with pytest.raises(BriefingError, match="analyses for"):
        await Synthesizer(llm=llm).run(make_selected(3), make_analyses(2), make_request())
    assert llm.requests == []


async def test_analysis_for_a_different_paper_is_rejected() -> None:
    analyses = make_analyses()
    analyses[1] = analyses[1].model_copy(update={"paper_id": "arxiv:9999.99999"})
    llm = FakeLLM([briefing_payload()])
    with pytest.raises(BriefingError, match="but the selected paper is"):
        await Synthesizer(llm=llm).run(make_selected(), analyses, make_request())
    assert llm.requests == []

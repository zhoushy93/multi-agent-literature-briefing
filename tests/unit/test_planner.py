"""Planner tests. The model is a stub, so nothing leaves the machine."""

from __future__ import annotations

import json
from typing import Any

import pytest

from briefing.agents.planner import PROMPT_NAME, Planner, render_time_window
from briefing.errors import SchemaValidationError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.schemas import TopicRequest


class FakeLLM:
    """Returns canned replies in order and records the requests."""

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the planner called the model more often than expected")
        return LLMResponse(text=self._replies.pop(0), prompt_tokens=1, completion_tokens=1)


def plan_payload(**overrides: Any) -> str:
    payload: dict[str, Any] = {
        "normalized_topic": "diffusion models for weather forecasting",
        "queries": [
            {
                "source": "arxiv",
                "q": 'all:"diffusion model" AND all:"weather forecasting"',
                "rationale": "recalls the core combination",
                "weight": 1.0,
            },
            {
                "source": "arxiv",
                "q": 'abs:"score-based" AND abs:forecasting',
                "rationale": "covers the method family",
                "weight": 0.6,
            },
        ],
        "keywords": ["diffusion model", "weather forecasting", "score-based generative model"],
        "inclusion_criteria": ["proposes or evaluates a generative forecasting model"],
        "exclusion_criteria": [],
        "time_window": [None, None],
    }
    payload.update(overrides)
    return json.dumps(payload)


def make_request(**overrides: Any) -> TopicRequest:
    data: dict[str, Any] = {
        "topic": "diffusion models for weather forecasting",
        "out_dir": "/tmp/out",
        "run_id": "r1",
    }
    data.update(overrides)
    return TopicRequest(**data)


async def test_returns_a_search_plan_from_the_model() -> None:
    llm = FakeLLM([plan_payload()])
    plan = await Planner(llm=llm).run(make_request())
    assert plan.normalized_topic == "diffusion models for weather forecasting"
    assert len(plan.queries) == 2
    assert plan.queries[0].source == "arxiv"
    assert len(plan.keywords) == 3
    assert plan.time_window == (None, None)


async def test_request_records_stage_temperature_and_prompt_version() -> None:
    llm = FakeLLM([plan_payload()])
    await Planner(llm=llm).run(make_request())
    request = llm.requests[0]
    assert request.stage == "S1"
    assert request.temperature == 0.0
    assert request.prompt_version == PROMPT_NAME
    assert request.json_output is True


async def test_non_english_topic_is_passed_through_and_english_is_demanded() -> None:
    llm = FakeLLM([plan_payload()])
    await Planner(llm=llm).run(make_request(topic="扩散模型用于天气预报", lang="zh"))
    user = llm.requests[0].user
    assert "扩散模型用于天气预报" in user, "the model must see the original topic"
    assert "zh" in user, "the report language must be visible"
    assert "in English" in user, "the English-query rule must reach the model"


async def test_sources_and_window_reach_the_model() -> None:
    llm = FakeLLM([plan_payload()])
    planner = Planner(llm=llm, available_sources=("arxiv", "crossref"))
    await planner.run(make_request(time_window=(2020, 2024)))
    user = llm.requests[0].user
    assert "arxiv, crossref" in user
    assert "2020 to 2024" in user


async def test_explicit_request_window_overrides_the_model() -> None:
    llm = FakeLLM([plan_payload(time_window=[1999, 2001])])
    plan = await Planner(llm=llm).run(make_request(time_window=(2020, 2024)))
    assert plan.time_window == (2020, 2024)


async def test_model_window_is_kept_when_the_request_is_open() -> None:
    llm = FakeLLM([plan_payload(time_window=[2015, None])])
    plan = await Planner(llm=llm).run(make_request())
    assert plan.time_window == (2015, None)


async def test_queries_and_keywords_are_trimmed() -> None:
    payload = json.loads(plan_payload())
    payload["queries"][0]["q"] = "  all:diffusion  "
    payload["queries"][0]["rationale"] = "  spaces  "
    payload["keywords"][0] = "  diffusion  "
    payload["normalized_topic"] = "  topic  "
    llm = FakeLLM([json.dumps(payload)])
    plan = await Planner(llm=llm).run(make_request())
    assert plan.queries[0].q == "all:diffusion"
    assert plan.queries[0].rationale == "spaces"
    assert plan.keywords[0] == "diffusion"
    assert plan.normalized_topic == "topic"


async def test_one_query_is_rejected_after_the_retry_budget() -> None:
    payload = json.loads(plan_payload())
    payload["queries"] = payload["queries"][:1]
    llm = FakeLLM([json.dumps(payload)] * 3)
    with pytest.raises(SchemaValidationError, match="SearchPlan"):
        await Planner(llm=llm).run(make_request())
    assert len(llm.requests) == 3, "two schema retries after the first attempt"


async def test_two_keywords_are_rejected() -> None:
    llm = FakeLLM([plan_payload(keywords=["one", "two"])] * 3)
    with pytest.raises(SchemaValidationError):
        await Planner(llm=llm).run(make_request())


async def test_unknown_source_name_is_rejected() -> None:
    payload = json.loads(plan_payload())
    payload["queries"][0]["source"] = "google_scholar"
    llm = FakeLLM([json.dumps(payload)] * 3)
    with pytest.raises(SchemaValidationError):
        await Planner(llm=llm).run(make_request())


async def test_extra_fields_are_rejected() -> None:
    llm = FakeLLM([plan_payload(invented_papers=["Fake Paper (2024)"])] * 3)
    with pytest.raises(SchemaValidationError):
        await Planner(llm=llm).run(make_request())


async def test_retry_feedback_reaches_the_model() -> None:
    payload = json.loads(plan_payload())
    payload["keywords"] = ["one"]
    llm = FakeLLM([json.dumps(payload), plan_payload()])
    plan = await Planner(llm=llm).run(make_request())
    assert len(plan.keywords) == 3
    assert "Validation errors" in llm.requests[1].user


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (None, "not specified; choose one"),
        ((2020, 2024), "2020 to 2024"),
        ((None, 2024), "earliest to 2024"),
        ((2020, None), "2020 to latest"),
    ],
)
def test_render_time_window(
    window: tuple[int | None, int | None] | None,
    expected: str,
) -> None:
    assert render_time_window(window) == expected

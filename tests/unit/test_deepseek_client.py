"""Client tests. Everything runs offline through respx or a fake client."""

from __future__ import annotations

from concurrent.futures import Future
from typing import Any

import httpx
import pytest
import respx
from tenacity import RetryCallState, wait_fixed

from briefing.errors import (
    BudgetExceeded,
    LLMRequestError,
    LLMTransientError,
    SchemaValidationError,
)
from briefing.llm.base import CompletionRequest, LLMResponse, complete_structured
from briefing.llm.budget import BudgetGuard
from briefing.llm.deepseek_client import DeepSeekClient, default_retry_wait, parse_retry_after
from briefing.schemas import Contract

BASE_URL = "https://api.deepseek.com"
ENDPOINT = f"{BASE_URL}/chat/completions"
MODEL = "test-model"


def make_budget(**kwargs: int) -> BudgetGuard:
    params: dict[str, int] = {"max_calls": 50, "max_tokens": 1_000_000}
    params.update(kwargs)
    return BudgetGuard(**params)


def make_client(budget: BudgetGuard | None = None, **kwargs: Any) -> DeepSeekClient:
    params: dict[str, Any] = {
        "api_key": "sk-test",
        "base_url": BASE_URL,
        "model": MODEL,
        "budget": budget if budget is not None else make_budget(),
        "retry_wait": wait_fixed(0),
        "max_retries": 2,
    }
    params.update(kwargs)
    return DeepSeekClient(**params)


def make_request(**kwargs: Any) -> CompletionRequest:
    params: dict[str, Any] = {
        "system": "You are terse.",
        "user": "Say something.",
        "temperature": 0.2,
        "stage": "S1",
        "prompt_version": "planner_v1",
    }
    params.update(kwargs)
    return CompletionRequest(**params)


def ok_body(
    content: str = '{"ok": true}',
    prompt: int = 12,
    completion: int = 7,
    finish: str = "stop",
) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": prompt, "completion_tokens": completion},
    }


# --- happy path --------------------------------------------------------------


@respx.mock
async def test_successful_call_returns_text_and_usage() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    client = make_client()
    try:
        response = await client.complete(make_request())
    finally:
        await client.aclose()
    assert response.text == '{"ok": true}'
    assert (response.prompt_tokens, response.completion_tokens) == (12, 7)
    assert response.finish_reason == "stop"


@respx.mock
async def test_finish_reason_is_carried_through() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body(finish="length")))
    client = make_client()
    try:
        response = await client.complete(make_request())
    finally:
        await client.aclose()
    assert response.finish_reason == "length"


@respx.mock
async def test_request_body_carries_model_params_and_json_mode() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    client = make_client()
    try:
        await client.complete(make_request(temperature=0.0, max_tokens=1024))
    finally:
        await client.aclose()

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["model"] == MODEL
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 1024
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]
    assert route.calls[0].request.headers["authorization"] == "Bearer sk-test"


@respx.mock
async def test_thinking_is_disabled_in_the_request_body() -> None:
    """Both models on this account reason; that eats the reply budget."""
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    client = make_client(thinking="disabled")
    try:
        await client.complete(make_request())
    finally:
        await client.aclose()

    import json

    body = json.loads(route.calls[0].request.content)
    assert body["thinking"] == {"type": "disabled"}


@respx.mock
async def test_thinking_is_left_alone_when_automatic() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    client = make_client(thinking="auto")
    try:
        await client.complete(make_request())
    finally:
        await client.aclose()

    import json

    assert "thinking" not in json.loads(route.calls[0].request.content)


@respx.mock
async def test_usage_is_recorded_in_the_budget() -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=ok_body(prompt=30, completion=4))
    )
    budget = make_budget()
    client = make_client(budget)
    try:
        await client.complete(make_request())
    finally:
        await client.aclose()
    snapshot = budget.snapshot()
    assert snapshot.calls == 1
    assert snapshot.total_tokens == 34
    assert snapshot.per_stage_calls == {"S1": 1}


# --- retries -----------------------------------------------------------------


@respx.mock
async def test_rate_limit_is_retried_then_succeeds() -> None:
    route = respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}),
            httpx.Response(200, json=ok_body()),
        ]
    )
    client = make_client()
    try:
        response = await client.complete(make_request())
    finally:
        await client.aclose()
    assert response.text == '{"ok": true}'
    assert route.call_count == 2


@respx.mock
async def test_server_errors_are_retried_until_the_limit() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    client = make_client(max_retries=2)
    try:
        with pytest.raises(LLMTransientError):
            await client.complete(make_request())
    finally:
        await client.aclose()
    assert route.call_count == 3  # initial attempt + 2 retries


@respx.mock
async def test_transport_timeout_is_retried() -> None:
    route = respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.ConnectTimeout("boom"),
            httpx.Response(200, json=ok_body()),
        ]
    )
    client = make_client()
    try:
        await client.complete(make_request())
    finally:
        await client.aclose()
    assert route.call_count == 2


@respx.mock
async def test_client_error_is_not_retried() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    client = make_client()
    try:
        with pytest.raises(LLMRequestError, match="400"):
            await client.complete(make_request())
    finally:
        await client.aclose()
    assert route.call_count == 1


@respx.mock
async def test_malformed_response_shape_is_a_request_error() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"choices": []}))
    client = make_client()
    try:
        with pytest.raises(LLMRequestError, match="unexpected response shape"):
            await client.complete(make_request())
    finally:
        await client.aclose()


# --- budget integration ------------------------------------------------------


@respx.mock
async def test_exhausted_budget_prevents_the_http_call() -> None:
    route = respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json=ok_body()))
    client = make_client(make_budget(max_calls=0))
    try:
        with pytest.raises(BudgetExceeded):
            await client.complete(make_request())
    finally:
        await client.aclose()
    assert route.call_count == 0


# --- retry-after handling ----------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [("3", 3.0), ("0", 0.0), ("-1", 0.0), ("Wed, 21 Oct 2026 07:28:00 GMT", None), (None, None)],
)
def test_parse_retry_after(header: str | None, expected: float | None) -> None:
    assert parse_retry_after(header) == expected


def make_retry_state(exc: BaseException | None) -> RetryCallState:
    state = RetryCallState(retry_object=None, fn=None, args=(), kwargs={})
    outcome: Future[Any] = Future()
    if exc is None:
        outcome.set_result(None)
    else:
        outcome.set_exception(exc)
    state.outcome = outcome
    return state


def test_retry_after_wins_over_backoff() -> None:
    state = make_retry_state(LLMTransientError("429", retry_after=3.0))
    assert default_retry_wait(state) == 3.0


def test_retry_after_is_capped() -> None:
    state = make_retry_state(LLMTransientError("429", retry_after=600.0))
    assert default_retry_wait(state) == 30.0


def test_backoff_is_used_when_there_is_no_retry_after() -> None:
    state = make_retry_state(LLMTransientError("503"))
    assert 0.0 <= default_retry_wait(state) <= 30.0


# --- structured output -------------------------------------------------------


class Thing(Contract):
    value: int


class FakeClient:
    """Returns canned replies in order and records the requests it received."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = list(texts)
        self.requests: list[CompletionRequest] = []
        self.finish_reason: str | None = "stop"

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._texts:
            raise AssertionError("the client was called more times than expected")
        return LLMResponse(
            text=self._texts.pop(0),
            prompt_tokens=1,
            completion_tokens=1,
            finish_reason=self.finish_reason,
        )


async def test_structured_output_retries_with_the_validation_error() -> None:
    fake = FakeClient(['{"value": "not an int"}', '{"value": 3}'])
    result = await complete_structured(fake, make_request(), Thing)
    assert result.value == 3
    assert len(fake.requests) == 2
    assert "Validation errors" in fake.requests[1].user
    assert "value" in fake.requests[1].user


async def test_structured_output_retries_on_malformed_json() -> None:
    fake = FakeClient(["not json at all", '{"value": 1}'])
    result = await complete_structured(fake, make_request(), Thing)
    assert result.value == 1
    assert "not valid JSON" in fake.requests[1].user


async def test_structured_output_gives_up_after_two_retries() -> None:
    fake = FakeClient(['{"nope": 1}'] * 3)
    with pytest.raises(SchemaValidationError):
        await complete_structured(fake, make_request(), Thing)
    assert len(fake.requests) == 3


async def test_structured_output_accepts_a_code_fence() -> None:
    fake = FakeClient(['```json\n{"value": 5}\n```'])
    assert (await complete_structured(fake, make_request(), Thing)).value == 5


async def test_post_validate_hook_can_reject_and_repair() -> None:
    """Context-dependent rules reuse the same retry loop and feedback channel."""
    fake = FakeClient(['{"value": 1}', '{"value": 2}'])

    def hook(model: Thing) -> Thing:
        if model.value != 2:
            raise ValueError("value must be 2")
        return model

    result = await complete_structured(fake, make_request(), Thing, post_validate=hook)
    assert result.value == 2
    assert len(fake.requests) == 2
    assert fake.requests[1].user.endswith("value must be 2")


async def test_post_validate_hook_passes_a_good_reply_through() -> None:
    fake = FakeClient(['{"value": 7}'])
    result = await complete_structured(
        fake, make_request(), Thing, post_validate=lambda model: model
    )
    assert result.value == 7
    assert len(fake.requests) == 1


async def test_a_truncated_reply_fails_fast_with_advice() -> None:
    """A cut-off reply is not retried: the same prompt truncates the same way."""
    fake = FakeClient(['{"value": 1}'])
    fake.finish_reason = "length"
    with pytest.raises(LLMRequestError, match="BRIEFING_MAX_OUTPUT_TOKENS"):
        await complete_structured(fake, make_request(), Thing)
    assert len(fake.requests) == 1, "retrying a truncated reply only spends money"

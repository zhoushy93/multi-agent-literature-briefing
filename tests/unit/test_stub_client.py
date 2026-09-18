"""Offline stub client tests (used by the Step 14 end-to-end run)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from briefing.errors import BudgetExceeded, StubExhaustedError
from briefing.llm.base import CompletionRequest
from briefing.llm.budget import BudgetGuard
from briefing.llm.stub_client import RecordedResponse, StubClient


def make_budget(**kwargs: int) -> BudgetGuard:
    params: dict[str, int] = {"max_calls": 50, "max_tokens": 1_000_000}
    params.update(kwargs)
    return BudgetGuard(**params)


def make_request(prompt_version: str | None = "planner_v1") -> CompletionRequest:
    return CompletionRequest(
        system="s",
        user="u",
        stage="S1",
        prompt_version=prompt_version,
    )


async def test_replays_responses_in_order() -> None:
    stub = StubClient(
        {"planner_v1": [RecordedResponse(text="first"), RecordedResponse(text="second")]},
        budget=make_budget(),
    )
    assert (await stub.complete(make_request())).text == "first"
    assert (await stub.complete(make_request())).text == "second"
    assert stub.calls == 2


async def test_exhausted_queue_is_an_error() -> None:
    stub = StubClient({"planner_v1": [RecordedResponse(text="only")]}, budget=make_budget())
    await stub.complete(make_request())
    with pytest.raises(StubExhaustedError, match="planner_v1"):
        await stub.complete(make_request())


async def test_unknown_key_is_an_error() -> None:
    stub = StubClient({"planner_v1": [RecordedResponse(text="x")]}, budget=make_budget())
    with pytest.raises(StubExhaustedError, match="screener_v1"):
        await stub.complete(make_request(prompt_version="screener_v1"))


async def test_budget_is_enforced_before_consuming_a_response() -> None:
    stub = StubClient({"planner_v1": [RecordedResponse(text="x")]}, budget=make_budget(max_calls=0))
    with pytest.raises(BudgetExceeded):
        await stub.complete(make_request())


async def test_usage_is_recorded() -> None:
    budget = make_budget()
    stub = StubClient(
        {"planner_v1": [RecordedResponse(text="x", prompt_tokens=9, completion_tokens=3)]},
        budget=budget,
    )
    await stub.complete(make_request())
    assert budget.snapshot().total_tokens == 12


async def test_fixtures_directory_is_loaded_in_filename_order(tmp_path: Path) -> None:
    (tmp_path / "planner_v1__001.json").write_text(
        json.dumps({"text": "one", "prompt_tokens": 1, "completion_tokens": 1}),
        encoding="utf-8",
    )
    (tmp_path / "planner_v1__002.json").write_text(json.dumps({"text": "two"}), encoding="utf-8")
    (tmp_path / "screener_v1__001.json").write_text(
        json.dumps({"text": "screen"}), encoding="utf-8"
    )

    stub = StubClient.from_fixtures_dir(tmp_path, budget=make_budget())
    assert (await stub.complete(make_request())).text == "one"
    assert (await stub.complete(make_request())).text == "two"
    assert (await stub.complete(make_request(prompt_version="screener_v1"))).text == "screen"


async def test_fixture_without_a_key_prefix_uses_the_default_queue(tmp_path: Path) -> None:
    (tmp_path / "fallback.json").write_text(json.dumps({"text": "any"}), encoding="utf-8")
    stub = StubClient.from_fixtures_dir(tmp_path, budget=make_budget())
    assert (await stub.complete(make_request(prompt_version=None))).text == "any"

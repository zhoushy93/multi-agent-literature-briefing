"""Budget guard tests (AGENTS.md §11, docs/architecture.md §6.3)."""

from __future__ import annotations

import pytest

from briefing.errors import BudgetExceeded
from briefing.llm.budget import BudgetGuard


def test_check_passes_while_under_budget() -> None:
    guard = BudgetGuard(max_calls=2, max_tokens=100)
    guard.check()
    guard.record(prompt_tokens=10, completion_tokens=5, stage="S1")
    guard.check()
    assert guard.calls == 1
    assert guard.total_tokens == 15
    assert guard.exceeded is False


def test_call_budget_stops_the_next_call() -> None:
    guard = BudgetGuard(max_calls=1, max_tokens=10_000)
    guard.check()
    guard.record(prompt_tokens=1, completion_tokens=1, stage="S1")
    with pytest.raises(BudgetExceeded, match="llm call budget exceeded"):
        guard.check()
    assert guard.exceeded is True


def test_zero_call_budget_blocks_the_first_call() -> None:
    guard = BudgetGuard(max_calls=0, max_tokens=10_000)
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_token_budget_is_flagged_on_record_and_raised_on_next_check() -> None:
    """Recording flags the overrun; the *next* check stops the run."""
    guard = BudgetGuard(max_calls=10, max_tokens=100)
    guard.check()
    guard.record(prompt_tokens=80, completion_tokens=40, stage="S5")
    assert guard.exceeded is True
    assert guard.reason is not None and "token budget exceeded" in guard.reason
    with pytest.raises(BudgetExceeded):
        guard.check()


def test_exceeded_state_is_sticky() -> None:
    guard = BudgetGuard(max_calls=1, max_tokens=10_000)
    guard.record(prompt_tokens=1, completion_tokens=1)
    with pytest.raises(BudgetExceeded) as first:
        guard.check()
    with pytest.raises(BudgetExceeded) as second:
        guard.check()
    assert str(first.value) == str(second.value)


def test_snapshot_reports_per_stage_calls() -> None:
    guard = BudgetGuard(max_calls=10, max_tokens=10_000)
    guard.record(prompt_tokens=10, completion_tokens=2, stage="S5")
    guard.record(prompt_tokens=20, completion_tokens=3, stage="S5")
    guard.record(prompt_tokens=30, completion_tokens=4, stage="S6")
    snapshot = guard.snapshot()
    assert snapshot.calls == 3
    assert snapshot.prompt_tokens == 60
    assert snapshot.completion_tokens == 9
    assert snapshot.total_tokens == 69
    assert snapshot.per_stage_calls == {"S5": 2, "S6": 1}
    assert snapshot.max_calls == 10
    assert snapshot.exceeded_reason is None


def test_record_without_stage_is_still_counted() -> None:
    guard = BudgetGuard(max_calls=10, max_tokens=10_000)
    guard.record(prompt_tokens=1, completion_tokens=1)
    snapshot = guard.snapshot()
    assert snapshot.calls == 1
    assert snapshot.per_stage_calls == {}

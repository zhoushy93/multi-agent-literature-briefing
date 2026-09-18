"""Pre-flight budget accounting (docs/architecture.md §6.3).

The guard is checked *before* every call, so a run stops at the boundary
instead of paying for one more request. ``record`` flags an overrun without
raising: the orchestrator finishes the current stage, persists its artifacts,
and reports ``budget_exceeded`` on the next check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from briefing.errors import BudgetExceeded


@dataclass(frozen=True)
class BudgetSnapshot:
    """Auditable budget state, surfaced in manifest.json."""

    calls: int
    prompt_tokens: int
    completion_tokens: int
    max_calls: int
    max_tokens: int
    per_stage_calls: dict[str, int] = field(default_factory=dict)
    exceeded_reason: str | None = None

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


class BudgetGuard:
    """Counts LLM calls and tokens for a single run."""

    def __init__(self, *, max_calls: int, max_tokens: int) -> None:
        self._max_calls = max_calls
        self._max_tokens = max_tokens
        self._calls = 0
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._per_stage_calls: dict[str, int] = {}
        self._exceeded_reason: str | None = None

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def total_tokens(self) -> int:
        return self._prompt_tokens + self._completion_tokens

    @property
    def exceeded(self) -> bool:
        return self._exceeded_reason is not None

    @property
    def reason(self) -> str | None:
        return self._exceeded_reason

    def check(self) -> None:
        """Raise if the next call would break the budget."""
        if self._exceeded_reason is not None:
            raise BudgetExceeded(self._exceeded_reason)
        if self._calls + 1 > self._max_calls:
            self._exceeded_reason = (
                f"llm call budget exceeded: {self._calls + 1} > {self._max_calls}"
            )
            raise BudgetExceeded(self._exceeded_reason)
        if self.total_tokens > self._max_tokens:
            self._exceeded_reason = (
                f"token budget exceeded: {self.total_tokens} > {self._max_tokens}"
            )
            raise BudgetExceeded(self._exceeded_reason)

    def record(
        self,
        *,
        prompt_tokens: int,
        completion_tokens: int,
        stage: str | None = None,
    ) -> None:
        """Account for one completed call."""
        self._calls += 1
        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        if stage is not None:
            self._per_stage_calls[stage] = self._per_stage_calls.get(stage, 0) + 1
        if self.total_tokens > self._max_tokens and self._exceeded_reason is None:
            self._exceeded_reason = (
                f"token budget exceeded: {self.total_tokens} > {self._max_tokens}"
            )

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            calls=self._calls,
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            max_calls=self._max_calls,
            max_tokens=self._max_tokens,
            per_stage_calls=dict(self._per_stage_calls),
            exceeded_reason=self._exceeded_reason,
        )

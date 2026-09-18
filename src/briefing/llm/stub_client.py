"""Offline client that replays recorded responses.

``BRIEFING_LLM_MODE=stub`` keeps the whole pipeline runnable without network
(AGENTS.md §10). Fixture files are named ``<key>__<NNN>.json``; ``key`` matches
``CompletionRequest.prompt_version`` (or ``default``) and ``NNN`` is the call
order within that key.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from briefing.errors import StubExhaustedError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.llm.budget import BudgetGuard

DEFAULT_KEY = "default"


@dataclass(frozen=True)
class RecordedResponse:
    text: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class StubClient:
    """Consumes queued responses in order, keyed by prompt version."""

    def __init__(
        self,
        responses: Mapping[str, Sequence[RecordedResponse]],
        *,
        budget: BudgetGuard,
    ) -> None:
        self._queues: dict[str, list[RecordedResponse]] = {
            key: list(values) for key, values in responses.items()
        }
        self._budget = budget
        # Named "received" rather than "requests" so it cannot be confused with
        # the requests HTTP library (which is banned in favour of httpx).
        self.received: list[CompletionRequest] = []

    @classmethod
    def from_fixtures_dir(cls, fixtures_dir: Path, *, budget: BudgetGuard) -> StubClient:
        """Load every ``*.json`` fixture under ``fixtures_dir``.

        A fixture holds either ``"text"`` (the raw reply) or ``"payload"`` (the
        reply as a JSON object). The payload form keeps recordings readable and
        impossible to escape wrong.
        """
        queues: dict[str, list[RecordedResponse]] = {}
        for path in sorted(fixtures_dir.glob("*.json")):
            key = path.name.split("__", 1)[0] if "__" in path.name else DEFAULT_KEY
            payload = json.loads(path.read_text(encoding="utf-8"))
            text = payload.get("text")
            if text is None:
                text = json.dumps(payload["payload"], ensure_ascii=False)
            queues.setdefault(key, []).append(
                RecordedResponse(
                    text=text,
                    prompt_tokens=int(payload.get("prompt_tokens", 0)),
                    completion_tokens=int(payload.get("completion_tokens", 0)),
                )
            )
        return cls(queues, budget=budget)

    @property
    def calls(self) -> int:
        return len(self.received)

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self._budget.check()
        key = request.prompt_version or DEFAULT_KEY
        queue = self._queues.get(key)
        if not queue:
            raise StubExhaustedError(
                f"no recorded response left for {key!r}; known keys: {sorted(self._queues)}"
            )
        recorded = queue.pop(0)
        self.received.append(request)
        self._budget.record(
            prompt_tokens=recorded.prompt_tokens,
            completion_tokens=recorded.completion_tokens,
            stage=request.stage,
        )
        return LLMResponse(
            text=recorded.text,
            prompt_tokens=recorded.prompt_tokens,
            completion_tokens=recorded.completion_tokens,
        )

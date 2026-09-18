"""S1 Planner: turn a topic into a runnable search plan.

The model is only asked for the plan. Everything mechanical — source
enumeration, time-window precedence, whitespace normalisation — is done here in
deterministic code (docs/architecture.md §4, S1).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import ValidationError

from briefing.errors import SchemaValidationError
from briefing.llm.base import (
    DEFAULT_MAX_TOKENS,
    CompletionRequest,
    LLMClient,
    complete_structured,
)
from briefing.prompts.loader import PromptLoader
from briefing.schemas import SearchPlan, TopicRequest

PROMPT_NAME = "planner_v1"

# AGENTS.md §6: temperature 0 for extraction-style work.
TEMPERATURE = 0.0

SYSTEM = "You are a research planning agent. You reply with a single JSON object and nothing else."


class Planner:
    """S1: produces the ``SearchPlan`` that drives retrieval."""

    name = "planner"

    def __init__(
        self,
        *,
        llm: LLMClient,
        available_sources: Sequence[str] = ("arxiv",),
        prompts: PromptLoader | None = None,
        temperature: float = TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._llm = llm
        self._available_sources = tuple(available_sources)
        self._prompts = prompts or PromptLoader()
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def run(self, request: TopicRequest) -> SearchPlan:
        prompt = self._prompts.load(PROMPT_NAME)
        user = prompt.render(
            topic=request.topic,
            lang=request.lang or "auto",
            available_sources=", ".join(self._available_sources),
            time_window=render_time_window(request.time_window),
        )
        plan = await complete_structured(
            self._llm,
            CompletionRequest(
                system=SYSTEM,
                user=user,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stage="S1",
                prompt_version=prompt.version,
            ),
            SearchPlan,
        )
        return self._post_process(plan, request)

    def _post_process(self, plan: SearchPlan, request: TopicRequest) -> SearchPlan:
        """Trim whitespace, then let an explicit request window win."""
        data = plan.model_dump()
        data["normalized_topic"] = plan.normalized_topic.strip()
        data["keywords"] = [keyword.strip() for keyword in plan.keywords]
        data["inclusion_criteria"] = [criterion.strip() for criterion in plan.inclusion_criteria]
        data["exclusion_criteria"] = [criterion.strip() for criterion in plan.exclusion_criteria]
        for query in data["queries"]:
            query["q"] = query["q"].strip()
            query["rationale"] = query["rationale"].strip()
        if request.time_window is not None:
            data["time_window"] = list(request.time_window)

        try:
            return SearchPlan.model_validate(data)
        except ValidationError as exc:
            raise SchemaValidationError(
                f"search plan became invalid after normalisation: {exc}"
            ) from exc


def render_time_window(time_window: tuple[int | None, int | None] | None) -> str:
    """Describe the requested window for the prompt."""
    if time_window is None:
        return "not specified; choose one"
    start, end = time_window
    return f"{start if start is not None else 'earliest'} to {end if end is not None else 'latest'}"

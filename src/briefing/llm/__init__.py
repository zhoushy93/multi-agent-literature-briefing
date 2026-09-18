"""LLM clients and the factory that picks one from configuration."""

from __future__ import annotations

from briefing.config import Settings
from briefing.errors import ConfigError
from briefing.llm.base import (
    CompletionRequest,
    LLMClient,
    LLMResponse,
    complete_structured,
    extract_json,
)
from briefing.llm.budget import BudgetGuard, BudgetSnapshot
from briefing.llm.deepseek_client import DeepSeekClient
from briefing.llm.stub_client import StubClient

__all__ = [
    "BudgetGuard",
    "BudgetSnapshot",
    "CompletionRequest",
    "DeepSeekClient",
    "LLMClient",
    "LLMResponse",
    "StubClient",
    "complete_structured",
    "create_llm_client",
    "extract_json",
]


def create_llm_client(settings: Settings, budget: BudgetGuard) -> LLMClient:
    """Build the client implied by ``settings.llm_mode``."""
    if settings.llm_mode == "stub":
        if settings.fixtures_dir is None:  # pragma: no cover - config validates this
            raise ConfigError("BRIEFING_FIXTURES_DIR is required when BRIEFING_LLM_MODE=stub")
        return StubClient.from_fixtures_dir(settings.fixtures_dir, budget=budget)

    if not settings.deepseek_api_key:
        raise ConfigError("DEEPSEEK_API_KEY is required when BRIEFING_LLM_MODE=live")

    return DeepSeekClient(
        api_key=settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        budget=budget,
        thinking=settings.deepseek_thinking,
        timeout_s=settings.deepseek_timeout_s,
        max_retries=settings.llm_max_retries,
    )

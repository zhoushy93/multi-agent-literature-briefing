"""Configuration.

AGENTS.md §9: every knob is read through this module with the ``BRIEFING_``
prefix. Provider credentials keep their native ``DEEPSEEK_API_KEY`` name.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, resolved from the environment (and ``.env``)."""

    model_config = SettingsConfigDict(
        env_prefix="BRIEFING_",
        env_file=".env",
        extra="ignore",
    )

    # Credentials use the provider's own variable name.
    deepseek_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DEEPSEEK_API_KEY", "BRIEFING_DEEPSEEK_API_KEY"),
    )

    # The default lives here and nowhere else: agents, prompts and tests must
    # take the model id from configuration (AGENTS.md §6). The value is the one
    # the account actually offers — see docs/decisions/003-deepseek-model-ids.md.
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model_reasoning: str | None = None
    # Both models on this account are reasoning models: their thinking tokens
    # are billed as output and can consume the whole reply budget, which is how
    # a long JSON answer ends up truncated. Structured extraction does not need
    # a chain of thought, so it is off by default (decisions/003).
    deepseek_thinking: Literal["auto", "disabled"] = "disabled"
    deepseek_timeout_s: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=5, ge=0, le=10)

    max_concurrency: int = Field(default=4, ge=1, le=32)
    max_llm_calls: int = Field(default=40, ge=1)
    max_tokens_budget: int = Field(default=400_000, ge=1)
    # AGENTS.md §7: the screener sees a bounded pool, never every candidate.
    max_candidates: int = Field(default=40, ge=5, le=200)
    # A truncated reply is unusable, and long candidate lists need headroom.
    max_output_tokens: int = Field(default=8192, ge=512, le=64_000)
    # arXiv rate-limits bursts with HTTP 406, so requests to one source are
    # spaced out and retried (AGENTS.md §7).
    source_min_interval_s: float = Field(default=3.0, ge=0.0, le=60.0)
    source_max_attempts: int = Field(default=4, ge=1, le=10)
    # OpenAlex puts clients that identify a contact address in its "polite
    # pool". Optional: without it the source still works.
    openalex_mailto: str | None = None

    llm_mode: Literal["live", "stub"] = "live"
    fixtures_dir: Path | None = None
    verify_semantic: bool = False

    @field_validator(
        "deepseek_api_key",
        "deepseek_model_reasoning",
        "fixtures_dir",
        "openalex_mailto",
        mode="before",
    )
    @classmethod
    def _blank_string_is_unset(cls, value: object) -> object:
        """Treat empty env values as unset, so a copied .env.example still works."""
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _stub_mode_requires_fixtures(self) -> Self:
        if self.llm_mode == "stub" and self.fixtures_dir is None:
            raise ValueError("BRIEFING_FIXTURES_DIR is required when BRIEFING_LLM_MODE=stub")
        return self

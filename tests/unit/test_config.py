"""Settings tests: the environment is the only source of truth."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from briefing.config import Settings

MANAGED_VARS = [
    "DEEPSEEK_API_KEY",
    "BRIEFING_DEEPSEEK_API_KEY",
    "BRIEFING_DEEPSEEK_MODEL",
    "BRIEFING_DEEPSEEK_BASE_URL",
    "BRIEFING_DEEPSEEK_MODEL_REASONING",
    "BRIEFING_MAX_CONCURRENCY",
    "BRIEFING_MAX_LLM_CALLS",
    "BRIEFING_MAX_TOKENS_BUDGET",
    "BRIEFING_LLM_MODE",
    "BRIEFING_FIXTURES_DIR",
    "BRIEFING_VERIFY_SEMANTIC",
    "MAX_CONCURRENCY",
]


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in MANAGED_VARS:
        monkeypatch.delenv(name, raising=False)


def load(**env: str) -> Settings:
    """Build settings from a clean slate, ignoring any .env on disk."""
    import os

    for key, value in env.items():
        os.environ[key] = value
    try:
        return Settings(_env_file=None)
    finally:
        for key in env:
            os.environ.pop(key, None)


def test_defaults_match_the_documented_guardrails() -> None:
    settings = load()
    assert settings.deepseek_model == "deepseek-v4-pro"
    assert settings.deepseek_base_url == "https://api.deepseek.com"
    assert settings.deepseek_model_reasoning is None
    assert settings.max_concurrency == 4
    assert settings.max_llm_calls == 40
    assert settings.max_tokens_budget == 400_000
    assert settings.max_candidates == 40
    assert settings.max_output_tokens == 8192
    assert settings.deepseek_thinking == "disabled"
    assert settings.source_min_interval_s == 3.0
    assert settings.source_max_attempts == 4
    assert settings.openalex_mailto is None
    assert settings.llm_mode == "live"
    assert settings.verify_semantic is False
    assert settings.deepseek_api_key is None


def test_prefixed_variables_override_defaults() -> None:
    settings = load(
        BRIEFING_DEEPSEEK_MODEL="some-other-model",
        BRIEFING_MAX_CONCURRENCY="8",
        BRIEFING_VERIFY_SEMANTIC="true",
    )
    assert settings.deepseek_model == "some-other-model"
    assert settings.max_concurrency == 8
    assert settings.verify_semantic is True


def test_unprefixed_variables_are_ignored() -> None:
    """AGENTS.md §9: project knobs are only read with the BRIEFING_ prefix."""
    settings = load(MAX_CONCURRENCY="9", DEEPSEEK_MODEL="ignored")
    assert settings.max_concurrency == 4
    assert settings.deepseek_model == "deepseek-v4-pro"


@pytest.mark.parametrize(
    "variable",
    ["DEEPSEEK_API_KEY", "BRIEFING_DEEPSEEK_API_KEY"],
)
def test_api_key_is_read_from_either_spelling(variable: str) -> None:
    assert load(**{variable: "sk-test"}).deepseek_api_key == "sk-test"


def test_blank_env_values_are_treated_as_unset() -> None:
    """A copied .env.example must not break the run."""
    settings = load(DEEPSEEK_API_KEY="", BRIEFING_FIXTURES_DIR="", BRIEFING_OPENALEX_MAILTO="")
    assert settings.deepseek_api_key is None
    assert settings.fixtures_dir is None
    assert settings.openalex_mailto is None


def test_stub_mode_requires_a_fixtures_directory() -> None:
    with pytest.raises(ValidationError):
        load(BRIEFING_LLM_MODE="stub")


def test_stub_mode_accepts_a_fixtures_directory() -> None:
    settings = load(BRIEFING_LLM_MODE="stub", BRIEFING_FIXTURES_DIR="/tmp/fixtures")
    assert settings.fixtures_dir == Path("/tmp/fixtures")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"BRIEFING_LLM_MODE": "record"},
        {"BRIEFING_MAX_CONCURRENCY": "0"},
        {"BRIEFING_MAX_CONCURRENCY": "33"},
        {"BRIEFING_MAX_LLM_CALLS": "0"},
        {"BRIEFING_MAX_TOKENS_BUDGET": "0"},
        {"BRIEFING_DEEPSEEK_TIMEOUT_S": "0"},
        {"BRIEFING_LLM_MAX_RETRIES": "11"},
        {"BRIEFING_MAX_CANDIDATES": "4"},
        {"BRIEFING_MAX_OUTPUT_TOKENS": "100"},
        {"BRIEFING_DEEPSEEK_THINKING": "always"},
        {"BRIEFING_SOURCE_MIN_INTERVAL_S": "61"},
        {"BRIEFING_SOURCE_MAX_ATTEMPTS": "0"},
    ],
)
def test_out_of_range_values_are_rejected(kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        load(**kwargs)

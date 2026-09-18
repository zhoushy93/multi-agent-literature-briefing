"""Paper sources."""

from __future__ import annotations

from pathlib import Path

from briefing.config import Settings
from briefing.errors import ConfigError
from briefing.sources.arxiv import ARXIV_ENDPOINT, ArxivSource
from briefing.sources.base import (
    DEFAULT_MAX_RESULTS,
    MAX_RESULTS_PER_REQUEST,
    USER_AGENT,
    FetchParams,
    SortBy,
    Source,
)
from briefing.sources.cache import DEFAULT_TTL_DAYS, CacheEntry, CacheStore
from briefing.sources.openalex import OpenAlexSource
from briefing.sources.replay import FixtureSource

__all__ = [
    "ARXIV_ENDPOINT",
    "DEFAULT_MAX_RESULTS",
    "DEFAULT_TTL_DAYS",
    "MAX_RESULTS_PER_REQUEST",
    "USER_AGENT",
    "ArxivSource",
    "CacheEntry",
    "CacheStore",
    "FetchParams",
    "FixtureSource",
    "OpenAlexSource",
    "SortBy",
    "Source",
    "create_source",
]


def create_sources(settings: Settings, cache: CacheStore) -> list[Source]:
    """The sources implied by configuration, in preference order.

    Stub mode means "no network at all", so it replays recorded responses for
    the sources as well as the model (see ``sources/replay.py``).

    In live mode arXiv comes first and OpenAlex second: the retriever falls back
    to the later entries when an earlier one is rate-limited or down.
    """
    if settings.llm_mode == "stub":
        if settings.fixtures_dir is None:  # pragma: no cover - config validates this
            raise ConfigError("BRIEFING_FIXTURES_DIR is required when BRIEFING_LLM_MODE=stub")
        return [FixtureSource(fixtures_dir=Path(settings.fixtures_dir))]
    return [
        ArxivSource(
            cache=cache,
            min_interval_s=settings.source_min_interval_s,
            max_attempts=settings.source_max_attempts,
        ),
        OpenAlexSource(cache=cache, mailto=settings.openalex_mailto),
    ]


def create_source(settings: Settings, cache: CacheStore) -> Source:
    """The preferred source only (kept for callers that want a single one)."""
    return create_sources(settings, cache)[0]

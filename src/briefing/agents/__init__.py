"""Pipeline agents. Each one is an independently testable async unit."""

from __future__ import annotations

from briefing.agents.analyzer import Analyzer, analyze_all
from briefing.agents.normalizer import MIN_ABSTRACT_CHARS, Normalizer, normalize
from briefing.agents.planner import Planner
from briefing.agents.screener import Screener
from briefing.agents.synthesizer import Synthesizer, detect_language
from briefing.evidence import quote_is_supported
from briefing.schemas import citation_key

__all__ = [
    "MIN_ABSTRACT_CHARS",
    "Analyzer",
    "Normalizer",
    "Planner",
    "Screener",
    "Synthesizer",
    "analyze_all",
    "citation_key",
    "detect_language",
    "normalize",
    "quote_is_supported",
]

"""Pydantic contracts shared by every agent.

These models are the only channel between agents (AGENTS.md §2.3): passing a
bare ``dict`` across an agent boundary is a design defect. Field names are the
contract, so renaming one is a breaking change that must update
``docs/architecture.md`` §3 in the same commit.

Deliberate non-goals — these are enforced by the Verifier (L1), not by parsing:

* ``Briefing.executive_summary`` length,
* ``Briefing.comparison`` row count versus paper count,
* ``Briefing.lang`` consistency with the topic.

If the schema rejected those, a policy violation would surface as a parse error
and merely retry the same prompt, instead of being reported as a ``Violation``
that drives the revision loop (docs/architecture.md §4, S7). Enforcing them
here would also make those verifier rules impossible to unit test, because no
invalid ``Briefing`` could be constructed.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# AGENTS.md §11: a run is capped at 3..5 papers.
MIN_PAPERS = 3
MAX_PAPERS = 5

# Enforced by Verifier L1, see the module docstring.
MAX_SUMMARY_CHARS = 120

# docs/architecture.md §3.2.
# docs/architecture.md §3.2. The briefing shows quotes of at most
# MAX_QUOTE_CHARS; the schema only guards against runaway output, because a
# model quoting a longer span is a formatting overflow, not a factual error.
MAX_QUOTE_CHARS = 300
QUOTE_HARD_LIMIT = 2000


def citation_key(index: int) -> str:
    """The citation key for the ``index``-th selected paper (1-based).

    The mapping is part of the contract: S6 assigns keys in
    ``SelectedPapers.items`` order and the renderer later maps ``Pk`` to
    ``[k]``. It lives here so the synthesizer and the deterministic verifier
    share one definition.
    """
    return f"P{index}"


def citation_keys(paper_count: int) -> list[str]:
    """The closed set of keys for a briefing over ``paper_count`` papers."""
    return [citation_key(index) for index in range(1, paper_count + 1)]


class Contract(BaseModel):
    """Base class for every contract: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")


# --- input and retrieval -----------------------------------------------------


class TopicRequest(Contract):
    """What the CLI hands to the orchestrator."""

    topic: str = Field(min_length=3, max_length=300)
    lang: Literal["zh", "en"] | None = None
    out_dir: Path
    min_papers: int = Field(default=MIN_PAPERS, ge=MIN_PAPERS, le=MAX_PAPERS)
    max_papers: int = Field(default=MAX_PAPERS, ge=MIN_PAPERS, le=MAX_PAPERS)
    time_window: tuple[int | None, int | None] | None = None
    no_cache: bool = False
    resume: bool = False
    run_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def _normalize_and_check(self) -> Self:
        stripped = self.topic.strip()
        if not stripped:
            raise ValueError("topic must not be blank")
        if self.min_papers > self.max_papers:
            raise ValueError("min_papers must be <= max_papers")
        self.topic = stripped
        if self.time_window is not None:
            start, end = self.time_window
            if start is not None and end is not None and start > end:
                raise ValueError("time_window start must be <= end")
        return self


class Query(Contract):
    """A single source-specific query produced by the Planner."""

    source: Literal["arxiv", "openalex", "crossref", "semantic_scholar"]
    q: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    weight: float = 1.0


class SearchPlan(Contract):
    """Planner output: queries plus the screening criteria."""

    normalized_topic: str = Field(min_length=1)
    queries: list[Query] = Field(min_length=2, max_length=6)
    keywords: list[str] = Field(min_length=3, max_length=12)
    inclusion_criteria: list[str] = Field(min_length=1, max_length=8)
    exclusion_criteria: list[str] = Field(default_factory=list, max_length=8)
    time_window: tuple[int | None, int | None]


class Paper(Contract):
    """Paper metadata. ``url`` must come from an API response, never from an LLM."""

    paper_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(min_length=1)
    year: int | None = Field(default=None, ge=1800, le=2100)
    venue: str | None = None
    origin: str = Field(min_length=1)
    url: str = Field(min_length=1)
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str = ""
    citation_count: int | None = Field(default=None, ge=0)
    open_access: bool | None = None
    retrieved_at: datetime


class CacheStats(Contract):
    """Retrieval cache accounting, surfaced in ``manifest.json``."""

    hits: int = Field(default=0, ge=0)
    misses: int = Field(default=0, ge=0)
    bypassed: bool = False


class CandidateList(Contract):
    """Retriever output."""

    papers: list[Paper] = Field(default_factory=list)
    queries_used: list[str] = Field(default_factory=list)
    per_source_counts: dict[str, int] = Field(default_factory=dict)
    cache: CacheStats
    dropped: dict[str, int] = Field(default_factory=dict)


class DedupedCandidates(Contract):
    """Normalizer output: deduplicated and pre-filtered candidates."""

    papers: list[Paper] = Field(default_factory=list)
    duplicates_removed: int = Field(default=0, ge=0)
    dropped: dict[str, int] = Field(default_factory=dict)
    # Carried forward so a later failure can still report what was searched for.
    queries_used: list[str] = Field(default_factory=list)


# --- screening and analysis --------------------------------------------------


class ScreenedPaper(Contract):
    """A candidate that survived screening, with its ranking metadata."""

    paper: Paper
    rank: int = Field(ge=1)
    relevance_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    matched_inclusion: list[str] = Field(default_factory=list)
    matched_exclusion: list[str] = Field(default_factory=list)


class SelectedPapers(Contract):
    """Screener output. The 3..5 bound is part of the product contract."""

    items: list[ScreenedPaper] = Field(min_length=MIN_PAPERS, max_length=MAX_PAPERS)
    selection_notes: str | None = None


class ScreenerChoice(Contract):
    """One LLM-facing selection, referring to a candidate by tag, never by id.

    The model never restates paper metadata: it picks ``C1``..``Cn`` and the
    Screener resolves the tag back to the ``Paper`` it was given, so a
    hallucinated title or URL cannot enter the pipeline.
    """

    tag: str = Field(min_length=1)
    rank: int = Field(ge=1)
    relevance_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    matched_inclusion: list[str] = Field(default_factory=list)
    matched_exclusion: list[str] = Field(default_factory=list)


class ScreenerResponse(Contract):
    """Raw Screener reply, before tags are resolved into ``SelectedPapers``."""

    selected: list[ScreenerChoice] = Field(min_length=1)
    selection_notes: str | None = None


class Evidence(Contract):
    """A quote traceable to the paper's own text."""

    field: Literal["abstract", "metadata", "fulltext_snippet"]
    locator: str = Field(min_length=1)
    quote: str = Field(min_length=1, max_length=QUOTE_HARD_LIMIT)


class PaperAnalysis(Contract):
    """Analyzer output for a single paper."""

    paper_id: str = Field(min_length=1)
    problem: str = Field(min_length=1)
    method: str = Field(min_length=1)
    data_and_experiments: str = Field(min_length=1)
    key_findings: list[str] = Field(min_length=1, max_length=6)
    limitations: list[str] = Field(min_length=1, max_length=5)
    # Why this paper matters for *this* topic. The analyzer is given the topic
    # precisely so it can answer that rather than repeat the abstract.
    relevance: str = Field(min_length=1)
    reusable_ideas: list[str] = Field(default_factory=list, max_length=5)
    evidence: list[Evidence] = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)


# --- briefing and verification ----------------------------------------------


class Claim(Contract):
    """A statement that must point at the papers supporting it."""

    text: str = Field(min_length=1)
    citation_keys: list[str] = Field(min_length=1)


class PaperCard(Contract):
    """One paper section of the report, keyed by ``P1``..``Pn``."""

    citation_key: str = Field(min_length=1)
    analysis: PaperAnalysis


class ComparisonRow(Contract):
    """One row of the cross-paper comparison table."""

    citation_key: str = Field(min_length=1)
    task: str = Field(min_length=1)
    method_family: str = Field(min_length=1)
    data: str = Field(min_length=1)
    metrics: str = Field(min_length=1)
    main_result: str = Field(min_length=1)


class Briefing(Contract):
    """Synthesizer output. Citation keys are resolved to [n] by the renderer."""

    run_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    lang: Literal["zh", "en"]
    executive_summary: str = Field(min_length=1)
    background_md: str = Field(min_length=1)
    method_md: str = Field(min_length=1)
    paper_cards: list[PaperCard] = Field(min_length=1)
    comparison: list[ComparisonRow]
    gaps_and_open_questions: list[Claim]
    further_reading: list[Claim]


class BriefingDraft(Contract):
    """What the synthesizer's model actually writes.

    It deliberately has no ``paper_cards``: the model used to re-emit the S5
    analyses inside the cards and would flatten their nested structure into
    prose, failing the whole run. The pipeline now attaches the verified
    analyses itself, so the cards cannot be corrupted at all.
    """

    run_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    lang: Literal["zh", "en"]
    executive_summary: str = Field(min_length=1)
    background_md: str = Field(min_length=1)
    method_md: str = Field(min_length=1)
    comparison: list[ComparisonRow]
    gaps_and_open_questions: list[Claim]
    further_reading: list[Claim]


class Violation(Contract):
    """A single verifier finding."""

    kind: Literal[
        "unknown_citation_key",
        "unknown_candidate",
        "unsupported_claim",
        "duplicate_reference",
        "missing_reference",
        "length_limit",
        "language_mismatch",
    ]
    severity: Literal["fatal", "warn"]
    location: str = Field(min_length=1)
    detail: str = Field(min_length=1)


class VerificationReport(Contract):
    """Verifier output, split into the deterministic and semantic layers."""

    passed: bool
    deterministic: list[Violation] = Field(default_factory=list)
    semantic: list[Violation] = Field(default_factory=list)
    revision_requested: bool = False


class SemanticVerification(Contract):
    """Raw L2 reply: findings only.

    It cannot carry a rewritten draft, so asking the model to "fix" the text is
    structurally impossible — repairing is the synthesizer's job, driven by
    these findings (docs/architecture.md §4, S7).
    """

    violations: list[Violation] = Field(default_factory=list)


__all__ = [
    "MAX_PAPERS",
    "MAX_QUOTE_CHARS",
    "MAX_SUMMARY_CHARS",
    "MIN_PAPERS",
    "QUOTE_HARD_LIMIT",
    "Briefing",
    "BriefingDraft",
    "CacheStats",
    "CandidateList",
    "Claim",
    "ComparisonRow",
    "Contract",
    "DedupedCandidates",
    "Evidence",
    "Paper",
    "PaperAnalysis",
    "PaperCard",
    "Query",
    "ScreenedPaper",
    "ScreenerChoice",
    "ScreenerResponse",
    "SearchPlan",
    "SelectedPapers",
    "SemanticVerification",
    "TopicRequest",
    "VerificationReport",
    "Violation",
    "citation_key",
    "citation_keys",
]

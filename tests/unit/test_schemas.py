"""Contract tests for briefing.schemas.

Every positive case proves the model can express what the pipeline must emit.
Every negative case pins a rule that the design documents call out, so that
loosening it later fails loudly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import BaseModel, ValidationError

import briefing.schemas as schemas

RETRIEVED_AT = datetime(2026, 9, 17, 9, 0, tzinfo=UTC)


def make_paper(**overrides: Any) -> schemas.Paper:
    data: dict[str, Any] = {
        "paper_id": "arxiv:2401.01234",
        "title": "Diffusion models for weather forecasting",
        "authors": ["A. Author", "B. Author"],
        "year": 2024,
        "venue": "arXiv",
        "origin": "arxiv",
        "url": "https://arxiv.org/abs/2401.01234",
        "arxiv_id": "2401.01234",
        "abstract": "We study diffusion models for weather forecasting.",
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return schemas.Paper(**data)


def make_evidence(**overrides: Any) -> schemas.Evidence:
    data: dict[str, Any] = {
        "field": "abstract",
        "locator": "abstract[0:40]",
        "quote": "We study diffusion models for weather forecasting.",
    }
    data.update(overrides)
    return schemas.Evidence(**data)


def make_analysis(**overrides: Any) -> schemas.PaperAnalysis:
    data: dict[str, Any] = {
        "paper_id": "arxiv:2401.01234",
        "problem": "Forecasting accuracy degrades at long horizons.",
        "method": "Conditional diffusion with a residual backbone.",
        "data_and_experiments": "ERA5 reanalysis, 1979-2020.",
        "key_findings": ["Beats the deterministic baseline at 7 days."],
        "limitations": ["Evaluated on a single region."],
        "relevance": "Relevant because it is the topic's core method.",
        "reusable_ideas": ["Noise schedule annealing"],
        "evidence": [make_evidence()],
        "confidence": 0.7,
    }
    data.update(overrides)
    return schemas.PaperAnalysis(**data)


def make_screened(rank: int = 1, **overrides: Any) -> schemas.ScreenedPaper:
    data: dict[str, Any] = {
        "paper": make_paper(),
        "rank": rank,
        "relevance_score": 0.9,
        "rationale": "Directly addresses the topic.",
        "matched_inclusion": ["Is about diffusion models for forecasting"],
    }
    data.update(overrides)
    return schemas.ScreenedPaper(**data)


def make_selected(count: int = 3) -> schemas.SelectedPapers:
    return schemas.SelectedPapers(items=[make_screened(rank=i) for i in range(1, count + 1)])


def make_briefing(**overrides: Any) -> schemas.Briefing:
    data: dict[str, Any] = {
        "run_id": "20260917T0900Z-diffusion-weather",
        "topic": "diffusion models for weather forecasting",
        "lang": "en",
        "executive_summary": "Diffusion models improve long-horizon forecasts.",
        "background_md": "## Background",
        "method_md": "## Method",
        "paper_cards": [schemas.PaperCard(citation_key="P1", analysis=make_analysis())],
        "comparison": [
            schemas.ComparisonRow(
                citation_key="P1",
                task="Forecasting",
                method_family="Diffusion",
                data="ERA5",
                metrics="RMSE",
                main_result="Lower RMSE at 7 days",
            )
        ],
        "gaps_and_open_questions": [
            schemas.Claim(text="Regional transfer is untested.", citation_keys=["P1"])
        ],
        "further_reading": [
            schemas.Claim(text="Read the ERA5 documentation.", citation_keys=["P1"])
        ],
    }
    data.update(overrides)
    return schemas.Briefing(**data)


# --- structural rules --------------------------------------------------------


def test_every_model_is_a_contract() -> None:
    """Every exported model must reject unknown fields (AGENTS.md §2.3)."""
    models = [
        getattr(schemas, name)
        for name in schemas.__all__
        if isinstance(getattr(schemas, name), type)
        and issubclass(getattr(schemas, name), BaseModel)
    ]
    assert models, "no models exported"
    for model in models:
        assert model.model_config.get("extra") == "forbid", f"{model.__name__} allows extras"


def test_unknown_top_level_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        schemas.Paper(**{**make_paper().model_dump(), "unexpected": 1})


def test_unknown_nested_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        schemas.ScreenedPaper(
            paper={**make_paper().model_dump(), "unexpected": 1},
            rank=1,
            relevance_score=0.5,
            rationale="r",
        )


# --- TopicRequest ------------------------------------------------------------


def test_topic_request_defaults_cap_papers_at_three_to_five() -> None:
    request = schemas.TopicRequest(topic="weather forecasting", out_dir="/tmp/out", run_id="r1")
    assert (request.min_papers, request.max_papers) == (3, 5)
    assert request.lang is None
    assert request.no_cache is False and request.resume is False


def test_topic_request_strips_the_topic() -> None:
    request = schemas.TopicRequest(topic="  weather  ", out_dir="/tmp/out", run_id="r1")
    assert request.topic == "weather"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"topic": "ab"},  # too short
        {"topic": "   "},  # blank after stripping
        {"min_papers": 4, "max_papers": 3},  # inverted range
        {"max_papers": 6},  # AGENTS.md §11 hard cap
        {"min_papers": 2},  # AGENTS.md §11 hard floor
        {"run_id": ""},  # required identifier
    ],
)
def test_topic_request_rejects_invalid_input(kwargs: dict[str, Any]) -> None:
    base: dict[str, Any] = {"topic": "weather forecasting", "out_dir": "/tmp/out", "run_id": "r1"}
    with pytest.raises(ValidationError):
        schemas.TopicRequest(**{**base, **kwargs})


def test_topic_request_rejects_inverted_time_window() -> None:
    with pytest.raises(ValidationError):
        schemas.TopicRequest(
            topic="weather forecasting",
            out_dir="/tmp/out",
            run_id="r1",
            time_window=(2024, 2019),
        )


# --- planner / retriever -----------------------------------------------------


def test_search_plan_requires_two_queries_and_three_keywords() -> None:
    query = schemas.Query(source="arxiv", q="all:diffusion", rationale="core term")
    with pytest.raises(ValidationError):
        schemas.SearchPlan(
            normalized_topic="t",
            queries=[query],
            keywords=["a", "b", "c"],
            inclusion_criteria=["i"],
            time_window=(None, None),
        )
    with pytest.raises(ValidationError):
        schemas.SearchPlan(
            normalized_topic="t",
            queries=[query, query],
            keywords=["a", "b"],
            inclusion_criteria=["i"],
            time_window=(None, None),
        )


def test_query_source_matches_the_design_doc() -> None:
    allowed = set(get_args(schemas.Query.model_fields["source"].annotation))
    assert allowed == {"arxiv", "openalex", "crossref", "semantic_scholar"}


@pytest.mark.parametrize("bad", [{"authors": []}, {"url": ""}, {"title": ""}, {"year": 0}])
def test_paper_requires_identifying_metadata(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_paper(**bad)


def test_candidate_list_round_trips_through_json() -> None:
    """Stage checkpoints are JSON on disk, so every contract must round-trip."""
    original = schemas.CandidateList(
        papers=[make_paper()],
        queries_used=["all:diffusion"],
        per_source_counts={"arxiv": 1},
        cache=schemas.CacheStats(hits=1, misses=2, bypassed=False),
        dropped={"no_abstract": 4},
    )
    restored = schemas.CandidateList.model_validate_json(original.model_dump_json())
    assert restored == original


def test_cache_stats_rejects_negative_counts() -> None:
    with pytest.raises(ValidationError):
        schemas.CacheStats(hits=-1)


# --- screening / analysis ----------------------------------------------------


@pytest.mark.parametrize("count", [0, 2, 6])
def test_selected_papers_enforces_three_to_five(count: int) -> None:
    with pytest.raises(ValidationError):
        make_selected(count)


def test_selected_papers_accepts_the_full_range() -> None:
    for count in (3, 4, 5):
        assert len(make_selected(count).items) == count


def test_screened_paper_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        make_screened(relevance_score=1.5)
    with pytest.raises(ValidationError):
        make_screened(rank=0)


def test_evidence_quote_accepts_more_than_the_display_limit() -> None:
    """A long quote is a formatting overflow: the analyzer trims it, the
    schema only guards against runaway output (see agent_architecture §3.2)."""
    assert len(make_evidence(quote="x" * schemas.MAX_QUOTE_CHARS).quote) == 300
    assert len(make_evidence(quote="x" * (schemas.MAX_QUOTE_CHARS + 120)).quote) == 420
    assert schemas.QUOTE_HARD_LIMIT > schemas.MAX_QUOTE_CHARS


def test_evidence_quote_is_capped_by_the_hard_limit() -> None:
    longest = schemas.QUOTE_HARD_LIMIT
    assert len(make_evidence(quote="x" * longest).quote) == longest
    with pytest.raises(ValidationError):
        make_evidence(quote="x" * (schemas.QUOTE_HARD_LIMIT + 1))


def test_evidence_field_matches_the_design_doc() -> None:
    allowed = set(get_args(schemas.Evidence.model_fields["field"].annotation))
    assert allowed == {"abstract", "metadata", "fulltext_snippet"}


@pytest.mark.parametrize(
    "bad",
    [
        {"key_findings": []},
        {"key_findings": ["a"] * 7},
        {"limitations": []},
        {"evidence": []},
        {"confidence": 1.1},
    ],
)
def test_analysis_field_ranges(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        make_analysis(**bad)


# --- briefing / verification -------------------------------------------------


def test_claim_must_cite_something() -> None:
    with pytest.raises(ValidationError):
        schemas.Claim(text="Something is true.", citation_keys=[])


def test_violation_kinds_match_the_design_doc() -> None:
    allowed = set(get_args(schemas.Violation.model_fields["kind"].annotation))
    assert allowed == {
        "unknown_citation_key",
        "unknown_candidate",
        "unsupported_claim",
        "duplicate_reference",
        "missing_reference",
        "length_limit",
        "language_mismatch",
    }


def test_violation_rejects_unknown_kind() -> None:
    with pytest.raises(ValidationError):
        schemas.Violation(
            kind="made_up_kind",  # type: ignore[arg-type]
            severity="fatal",
            location="comparison[0]",
            detail="d",
        )


def test_verification_report_defaults_to_no_findings() -> None:
    report = schemas.VerificationReport(passed=True)
    assert report.deterministic == []
    assert report.semantic == []
    assert report.revision_requested is False


def test_briefing_accepts_a_valid_payload() -> None:
    briefing = make_briefing()
    assert briefing.lang == "en"
    assert briefing.paper_cards[0].citation_key == "P1"


def test_long_summary_is_accepted_here_and_left_to_the_verifier() -> None:
    """The schema must not reject what Verifier L1 is meant to report.

    If it did, the revision loop could never be exercised and L1's
    length_limit rule could not be unit tested (see the schemas docstring).
    """
    briefing = make_briefing(executive_summary="x" * (schemas.MAX_SUMMARY_CHARS + 50))
    assert len(briefing.executive_summary) > schemas.MAX_SUMMARY_CHARS


def test_comparison_row_mismatch_is_left_to_the_verifier() -> None:
    briefing = make_briefing(comparison=[])
    assert len(briefing.comparison) != len(briefing.paper_cards)

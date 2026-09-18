"""Verifier L1 tests: every rule, plus the no-LLM guarantee."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from briefing.schemas import (
    MAX_SUMMARY_CHARS,
    Briefing,
    Claim,
    ComparisonRow,
    Paper,
    PaperAnalysis,
    PaperCard,
    ScreenedPaper,
    SelectedPapers,
)
from briefing.verify.deterministic import verify_deterministic

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "briefing" / "verify" / "deterministic.py"
)


def paper_id(index: int) -> str:
    return f"arxiv:2401.0000{index}"


def abstract(index: int) -> str:
    return f"We study diffusion models number {index} for weather forecasting on ERA5."


def make_paper(index: int, **overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": paper_id(index),
        "title": f"Study {index}",
        "authors": ["Alice Author"],
        "year": 2024,
        "venue": "Journal of Weather ML",
        "origin": "arxiv",
        "url": f"https://arxiv.org/abs/2401.0000{index}",
        "arxiv_id": f"2401.0000{index}",
        "abstract": abstract(index),
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_selected(count: int = 3) -> SelectedPapers:
    return SelectedPapers(
        items=[
            ScreenedPaper(
                paper=make_paper(index),
                rank=index,
                relevance_score=0.9,
                rationale="relevant",
            )
            for index in range(1, count + 1)
        ]
    )


def make_analysis(index: int, **overrides: Any) -> PaperAnalysis:
    data: dict[str, Any] = {
        "paper_id": paper_id(index),
        "problem": "A problem.",
        "method": "A method.",
        "data_and_experiments": "Some data.",
        "key_findings": ["A finding."],
        "limitations": ["A limitation."],
        "relevance": "Relevant to the topic under review.",
        "reusable_ideas": [],
        "evidence": [
            {
                "field": "abstract",
                "locator": "abstract[0:40]",
                "quote": f"We study diffusion models number {index}",
            }
        ],
        "confidence": 0.6,
    }
    data.update(overrides)
    return PaperAnalysis(**data)


def make_card(key: str, index: int, **overrides: Any) -> PaperCard:
    data: dict[str, Any] = {"citation_key": key, "analysis": make_analysis(index)}
    data.update(overrides)
    return PaperCard(**data)


def make_row(key: str) -> ComparisonRow:
    return ComparisonRow(
        citation_key=key,
        task="Forecasting",
        method_family="Diffusion",
        data="ERA5",
        metrics="RMSE",
        main_result="Lower error",
    )


def make_briefing(**overrides: Any) -> Briefing:
    data: dict[str, Any] = {
        "run_id": "20260917T1200Z-diffusion",
        "topic": "diffusion models for weather forecasting",
        "lang": "en",
        "executive_summary": "Diffusion models improve long-range forecasts.",
        "background_md": "## Background",
        "method_md": "## Method",
        "paper_cards": [make_card(f"P{index}", index) for index in (1, 2, 3)],
        "comparison": [make_row(f"P{index}") for index in (1, 2, 3)],
        "gaps_and_open_questions": [
            Claim(text="Regional transfer is untested.", citation_keys=["P1"])
        ],
        "further_reading": [],
    }
    data.update(overrides)
    return Briefing(**data)


def kinds(briefing: Briefing, selected: SelectedPapers | None = None) -> list[str]:
    return [
        violation.kind for violation in verify_deterministic(briefing, selected or make_selected())
    ]


def locations(briefing: Briefing) -> list[str]:
    return [violation.location for violation in verify_deterministic(briefing, make_selected())]


# --- the compliant case ------------------------------------------------------


def test_a_compliant_briefing_has_no_violations() -> None:
    assert verify_deterministic(make_briefing(), make_selected()) == []


def test_findings_are_deterministic() -> None:
    broken = make_briefing(executive_summary="x" * (MAX_SUMMARY_CHARS + 1), comparison=[])
    first = verify_deterministic(broken, make_selected())
    second = verify_deterministic(broken, make_selected())
    assert first == second


def test_every_finding_is_fatal() -> None:
    broken = make_briefing(comparison=[])
    assert {violation.severity for violation in verify_deterministic(broken, make_selected())} == {
        "fatal"
    }


def test_the_deterministic_layer_does_not_import_llm_code() -> None:
    """L1 must stay LLM-free, so it can always run (TASK.md Step 10)."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not [name for name in imported if name.startswith(("briefing.llm", "briefing.agents"))]


# --- citation keys -----------------------------------------------------------


def test_unknown_key_in_a_card_is_reported() -> None:
    briefing = make_briefing(
        paper_cards=[make_card("P1", 1), make_card("P2", 2), make_card("P9", 3)],
    )
    assert "unknown_citation_key" in kinds(briefing)
    assert "paper_cards[2].citation_key" in locations(briefing)


def test_unknown_key_in_a_comparison_row_is_reported() -> None:
    briefing = make_briefing(
        comparison=[make_row("P1"), make_row("P2"), make_row("P7")],
    )
    assert "unknown_citation_key" in kinds(briefing)
    assert "comparison[2].citation_key" in locations(briefing)


def test_unknown_key_in_a_claim_is_reported() -> None:
    briefing = make_briefing(
        gaps_and_open_questions=[Claim(text="Untested.", citation_keys=["P4"])],
    )
    assert "unknown_citation_key" in kinds(briefing)
    assert "gaps_and_open_questions[0].citation_keys" in locations(briefing)


# --- coverage ----------------------------------------------------------------


def test_missing_card_is_reported() -> None:
    briefing = make_briefing(paper_cards=[make_card("P1", 1), make_card("P2", 2)])
    findings = verify_deterministic(briefing, make_selected())
    assert "length_limit" in {finding.kind for finding in findings}
    assert any("no card for P3" in finding.detail for finding in findings)


def test_duplicate_card_is_reported() -> None:
    briefing = make_briefing(
        paper_cards=[make_card("P1", 1), make_card("P1", 2), make_card("P2", 3)],
    )
    findings = verify_deterministic(briefing, make_selected())
    assert any(
        finding.kind == "duplicate_reference" and "P1 appears 2 times" in finding.detail
        for finding in findings
    )


def test_extra_card_is_reported() -> None:
    briefing = make_briefing(
        paper_cards=[
            make_card("P1", 1),
            make_card("P2", 2),
            make_card("P3", 3),
            make_card("P1", 1),
        ],
    )
    findings = verify_deterministic(briefing, make_selected())
    assert any(finding.kind == "length_limit" for finding in findings)


def test_missing_comparison_row_is_reported() -> None:
    briefing = make_briefing(comparison=[make_row("P1"), make_row("P2")])
    findings = verify_deterministic(briefing, make_selected())
    assert any("no comparison row for P3" in finding.detail for finding in findings)


def test_duplicate_comparison_row_is_reported() -> None:
    briefing = make_briefing(
        comparison=[make_row("P1"), make_row("P1"), make_row("P2"), make_row("P3")],
    )
    findings = verify_deterministic(briefing, make_selected())
    assert any(
        finding.kind == "duplicate_reference" and finding.location == "comparison"
        for finding in findings
    )


# --- key to paper mapping ----------------------------------------------------


def test_card_bound_to_the_wrong_paper_is_reported() -> None:
    """P1 must carry paper 1's analysis, not paper 2's."""
    briefing = make_briefing(
        paper_cards=[make_card("P1", 2), make_card("P2", 2), make_card("P3", 3)],
    )
    findings = verify_deterministic(briefing, make_selected())
    assert any(
        finding.kind == "missing_reference"
        and finding.location == "paper_cards[0].analysis.paper_id"
        for finding in findings
    )


# --- summary and language ----------------------------------------------------


def test_summary_at_the_limit_is_accepted() -> None:
    briefing = make_briefing(executive_summary="x" * MAX_SUMMARY_CHARS)
    assert kinds(briefing) == []


def test_summary_over_the_limit_is_reported() -> None:
    briefing = make_briefing(executive_summary="x" * (MAX_SUMMARY_CHARS + 1))
    findings = verify_deterministic(briefing, make_selected())
    assert [(finding.kind, finding.location) for finding in findings] == [
        ("length_limit", "executive_summary")
    ]
    assert str(MAX_SUMMARY_CHARS) in findings[0].detail


def test_chinese_briefing_without_chinese_text_is_reported() -> None:
    briefing = make_briefing(lang="zh")
    assert "language_mismatch" in kinds(briefing)


def test_english_briefing_without_latin_text_is_reported() -> None:
    briefing = make_briefing(lang="en", executive_summary="扩散模型提升长期预报精度。")
    assert "language_mismatch" in kinds(briefing)


def test_chinese_briefing_with_chinese_text_is_accepted() -> None:
    briefing = make_briefing(lang="zh", executive_summary="扩散模型提升了长期天气预报的精度。")
    assert kinds(briefing) == []


def test_expected_lang_mismatch_is_reported() -> None:
    findings = verify_deterministic(make_briefing(), make_selected(), expected_lang="zh")
    assert [(finding.kind, finding.location) for finding in findings] == [
        ("language_mismatch", "lang")
    ]


def test_expected_lang_match_is_accepted() -> None:
    assert verify_deterministic(make_briefing(), make_selected(), expected_lang="en") == []


# --- evidence provenance -----------------------------------------------------


def test_quote_not_in_the_source_paper_is_reported() -> None:
    bad_card = make_card(
        "P1",
        1,
        analysis=make_analysis(
            1,
            evidence=[{"field": "abstract", "locator": "abstract[0:20]", "quote": "invented text"}],
        ),
    )
    briefing = make_briefing(
        paper_cards=[bad_card, make_card("P2", 2), make_card("P3", 3)],
    )
    findings = verify_deterministic(briefing, make_selected())
    assert [
        (finding.kind, finding.location)
        for finding in findings
        if finding.kind == "unsupported_claim"
    ] == [("unsupported_claim", "paper_cards[0].analysis.evidence[0]")]


def test_quote_from_metadata_is_accepted() -> None:
    card = make_card(
        "P1",
        1,
        analysis=make_analysis(
            1,
            evidence=[
                {
                    "field": "metadata",
                    "locator": "venue",
                    "quote": "Journal of Weather ML",
                }
            ],
        ),
    )
    briefing = make_briefing(paper_cards=[card, make_card("P2", 2), make_card("P3", 3)])
    assert verify_deterministic(briefing, make_selected()) == []


def test_quote_from_another_paper_is_reported() -> None:
    """P1 quoting paper 2's abstract is a cross-paper mix-up."""
    card = make_card(
        "P1",
        1,
        analysis=make_analysis(
            1,
            evidence=[
                {
                    "field": "abstract",
                    "locator": "abstract[0:40]",
                    "quote": "We study diffusion models number 2",
                }
            ],
        ),
    )
    briefing = make_briefing(paper_cards=[card, make_card("P2", 2), make_card("P3", 3)])
    assert "unsupported_claim" in kinds(briefing)

"""Verifier L2 tests, plus the revision policy that consumes its findings."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from briefing.config import Settings
from briefing.errors import BriefingError
from briefing.llm.base import CompletionRequest, LLMResponse
from briefing.schemas import (
    Briefing,
    Claim,
    ComparisonRow,
    Paper,
    PaperAnalysis,
    PaperCard,
    ScreenedPaper,
    SelectedPapers,
    Violation,
)
from briefing.verify.report import build_report, fatal_findings, resolve_outcome
from briefing.verify.semantic import (
    PROMPT_NAME,
    SemanticVerifier,
    collect_claims,
    create_semantic_verifier,
)

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


class FakeLLM:
    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._replies:
            raise AssertionError("the verifier called the model more often than expected")
        return LLMResponse(text=self._replies.pop(0), prompt_tokens=1, completion_tokens=1)


def paper_id(index: int) -> str:
    return f"arxiv:2401.0000{index}"


def make_paper(index: int) -> Paper:
    return Paper(
        paper_id=paper_id(index),
        title=f"Study {index}",
        authors=["Alice Author"],
        year=2024,
        venue="Journal of Weather ML",
        origin="arxiv",
        url=f"https://arxiv.org/abs/2401.0000{index}",
        arxiv_id=f"2401.0000{index}",
        abstract=f"We study diffusion models number {index} on ERA5 reanalysis.",
        retrieved_at=RETRIEVED_AT,
    )


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
        "problem": f"Problem {index}",
        "method": f"Method {index}",
        "data_and_experiments": f"Dataset {index}",
        "key_findings": [f"Finding {index}"],
        "limitations": [f"Limitation {index}"],
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


def make_analyses(count: int = 3) -> list[PaperAnalysis]:
    return [make_analysis(index) for index in range(1, count + 1)]


def make_briefing(**overrides: Any) -> Briefing:
    data: dict[str, Any] = {
        "run_id": "20260917T1200Z-diffusion",
        "topic": "diffusion models for weather forecasting",
        "lang": "en",
        "executive_summary": "Diffusion models improve long-range forecasts.",
        "background_md": "## Background",
        "method_md": "## Method",
        "paper_cards": [
            PaperCard(citation_key=f"P{index}", analysis=make_analysis(index))
            for index in (1, 2, 3)
        ],
        "comparison": [
            ComparisonRow(
                citation_key=f"P{index}",
                task="Forecasting",
                method_family="Diffusion",
                data="ERA5",
                metrics="RMSE",
                main_result="Lower error",
            )
            for index in (1, 2, 3)
        ],
        "gaps_and_open_questions": [
            Claim(text="Regional transfer is untested.", citation_keys=["P1", "P3"])
        ],
        "further_reading": [Claim(text="Start with P2.", citation_keys=["P2"])],
    }
    data.update(overrides)
    return Briefing(**data)


def finding(
    location: str = "gaps_and_open_questions[0]",
    *,
    kind: str = "unsupported_claim",
    severity: str = "fatal",
    detail: str = "P1 does not test regional transfer.",
) -> dict[str, str]:
    return {"kind": kind, "severity": severity, "location": location, "detail": detail}


def payload(findings: list[dict[str, str]]) -> str:
    return json.dumps({"violations": findings})


async def run_l2(
    replies: list[str],
    *,
    briefing: Briefing | None = None,
    selected: SelectedPapers | None = None,
    analyses: list[PaperAnalysis] | None = None,
) -> tuple[list[Violation], SemanticVerifier, FakeLLM]:
    llm = FakeLLM(replies)
    verifier = SemanticVerifier(llm=llm)
    findings = await verifier.run(
        briefing or make_briefing(),
        selected or make_selected(),
        analyses if analyses is not None else make_analyses(),
    )
    return findings, verifier, llm


# --- claim list --------------------------------------------------------------


def test_claims_cover_gaps_further_reading_and_key_findings() -> None:
    locations = [claim.location for claim in collect_claims(make_briefing())]
    assert locations == [
        "gaps_and_open_questions[0]",
        "further_reading[0]",
        "paper_cards[0].analysis.key_findings[0]",
        "paper_cards[1].analysis.key_findings[0]",
        "paper_cards[2].analysis.key_findings[0]",
    ]


def test_claims_carry_their_citation_keys() -> None:
    claims = collect_claims(make_briefing())
    assert claims[0].citation_keys == ["P1", "P3"]
    assert claims[2].citation_keys == ["P1"]
    assert claims[0].render() == (
        "[gaps_and_open_questions[0]] (cites P1, P3): Regional transfer is untested."
    )


# --- audit -------------------------------------------------------------------


async def test_supported_claims_produce_no_findings() -> None:
    findings, _, _ = await run_l2([payload([])])
    assert findings == []


async def test_unsupported_claim_is_reported() -> None:
    findings, _, _ = await run_l2([payload([finding()])])
    assert [item.location for item in findings] == ["gaps_and_open_questions[0]"]
    assert findings[0].kind == "unsupported_claim"


async def test_warn_findings_are_kept() -> None:
    findings, _, _ = await run_l2([payload([finding(severity="warn")])])
    assert findings[0].severity == "warn"


async def test_finding_for_an_unknown_location_is_dropped() -> None:
    findings, verifier, _ = await run_l2([payload([finding(location="invented[7]")])])
    assert findings == []
    assert len(verifier.dropped) == 1
    assert "not a claim that was audited" in verifier.dropped[0]


async def test_non_semantic_kind_is_dropped() -> None:
    """L2 must not re-open questions the deterministic layer already settled."""
    findings, verifier, _ = await run_l2(
        [payload([finding(kind="length_limit", location="executive_summary")])]
    )
    assert findings == []
    assert "not a semantic finding" in verifier.dropped[0]


async def test_a_good_finding_survives_alongside_a_dropped_one() -> None:
    findings, verifier, _ = await run_l2(
        [payload([finding(), finding(kind="duplicate_reference")])]
    )
    assert len(findings) == 1
    assert len(verifier.dropped) == 1


# --- prompt ------------------------------------------------------------------


async def test_prompt_carries_the_catalogue_and_every_claim() -> None:
    _, _, llm = await run_l2([payload([])])
    user = llm.requests[0].user
    assert "Allowed citation keys: P1, P2, P3" in user
    assert "[P1] Study 1" in user
    assert "[gaps_and_open_questions[0]] (cites P1, P3)" in user
    assert "[paper_cards[2].analysis.key_findings[0]]" in user


async def test_catalogue_includes_abstracts_for_auditing() -> None:
    _, _, llm = await run_l2([payload([])])
    expected = "Abstract: We study diffusion models number 1 on ERA5 reanalysis."
    assert expected in llm.requests[0].user


async def test_request_records_stage_temperature_and_prompt_version() -> None:
    _, _, llm = await run_l2([payload([])])
    request = llm.requests[0]
    assert request.stage == "S7"
    assert request.temperature == 0.0
    assert request.prompt_version == PROMPT_NAME


async def test_analysis_mismatch_is_rejected_before_the_call() -> None:
    llm = FakeLLM([payload([])])
    with pytest.raises(BriefingError, match="analyses for"):
        await SemanticVerifier(llm=llm).run(make_briefing(), make_selected(3), make_analyses(2))
    assert llm.requests == []


# --- revision policy ---------------------------------------------------------


def test_revision_once_triggers_a_single_revision() -> None:
    report = build_report(semantic=[Violation(**finding())])
    assert report.passed is False
    assert report.revision_requested is True
    assert resolve_outcome(report) == "revise"
    assert [item.location for item in fatal_findings(report)] == ["gaps_and_open_questions[0]"]


def test_second_failure_ends_the_run() -> None:
    """Same findings, but the one revision is already spent."""
    report = build_report(semantic=[Violation(**finding())], revision_used=True)
    assert report.passed is False
    assert report.revision_requested is False
    assert resolve_outcome(report) == "verification_failed"


def test_a_clean_report_is_verified() -> None:
    report = build_report()
    assert report.passed is True
    assert report.revision_requested is False
    assert resolve_outcome(report) == "verified"


def test_warnings_alone_never_block() -> None:
    report = build_report(
        deterministic=[Violation(**finding(kind="length_limit", severity="warn"))],
        semantic=[Violation(**finding(severity="warn"))],
        revision_used=True,
    )
    assert report.passed is True
    assert resolve_outcome(report) == "verified"
    assert fatal_findings(report) == []


def test_fatal_findings_come_from_both_layers() -> None:
    report = build_report(
        deterministic=[Violation(**finding(kind="missing_reference", location="paper_cards"))],
        semantic=[Violation(**finding())],
    )
    assert {item.location for item in fatal_findings(report)} == {
        "paper_cards",
        "gaps_and_open_questions[0]",
    }


# --- feature switch ----------------------------------------------------------


def test_disabled_semantic_verifier_is_not_created() -> None:
    settings = Settings(_env_file=None, verify_semantic=False)
    assert create_semantic_verifier(settings, FakeLLM([])) is None


def test_enabled_semantic_verifier_is_created() -> None:
    settings = Settings(_env_file=None, verify_semantic=True)
    assert isinstance(create_semantic_verifier(settings, FakeLLM([])), SemanticVerifier)


def test_enabled_but_missing_client_is_not_a_crash() -> None:
    settings = Settings(_env_file=None, verify_semantic=True)
    assert create_semantic_verifier(settings, None) is None

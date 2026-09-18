"""S7 L1: the deterministic half of the verifier.

No LLM, no IO, no clock. Given a ``Briefing`` and the ``SelectedPapers`` it is
meant to describe, it returns every breach it can *prove*. This layer is always
enabled; only L2 can be switched off (docs/architecture.md §4, S7).

Every finding is fatal: these are contract breaches, not style notes. The
orchestrator turns them into a single revision request, and a second failure
ends the run with ``verification_failed``.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from briefing.evidence import (
    build_haystack,
    contains_cjk,
    contains_latin,
    quote_is_supported,
)
from briefing.schemas import (
    MAX_SUMMARY_CHARS,
    Briefing,
    SelectedPapers,
    Violation,
    citation_keys,
)

SEVERITY: Literal["fatal"] = "fatal"


def verify_deterministic(
    briefing: Briefing,
    selected: SelectedPapers,
    *,
    summary_char_limit: int = MAX_SUMMARY_CHARS,
    expected_lang: Literal["zh", "en"] | None = None,
) -> list[Violation]:
    """Return every deterministic contract breach, in a stable order."""
    expected = citation_keys(len(selected.items))
    violations: list[Violation] = []
    violations.extend(_check_cards(briefing, selected, expected))
    violations.extend(_check_comparison(briefing, expected))
    violations.extend(_check_claims(briefing, expected))
    violations.extend(_check_summary(briefing, summary_char_limit))
    violations.extend(_check_language(briefing, expected_lang))
    violations.extend(_check_evidence(briefing, selected, expected))
    return violations


# --- individual rules --------------------------------------------------------


def _check_cards(
    briefing: Briefing,
    selected: SelectedPapers,
    expected: list[str],
) -> list[Violation]:
    """Coverage, uniqueness, key validity, and key -> paper_id mapping."""
    violations: list[Violation] = []
    cards = briefing.paper_cards
    allowed = set(expected)

    if len(cards) != len(selected.items):
        violations.append(
            Violation(
                kind="length_limit",
                severity=SEVERITY,
                location="paper_cards",
                detail=f"expected {len(selected.items)} cards, found {len(cards)}",
            )
        )

    keys = [card.citation_key for card in cards]
    for index, key in enumerate(keys):
        if key not in allowed:
            violations.append(
                Violation(
                    kind="unknown_citation_key",
                    severity=SEVERITY,
                    location=f"paper_cards[{index}].citation_key",
                    detail=f"{key!r} is not one of {', '.join(expected)}",
                )
            )

    for key, count in sorted(Counter(keys).items()):
        if count > 1:
            violations.append(
                Violation(
                    kind="duplicate_reference",
                    severity=SEVERITY,
                    location="paper_cards",
                    detail=f"{key} appears {count} times",
                )
            )

    for key in expected:
        if key not in keys:
            violations.append(
                Violation(
                    kind="missing_reference",
                    severity=SEVERITY,
                    location="paper_cards",
                    detail=f"no card for {key}",
                )
            )

    for index, card in enumerate(cards):
        position = _position_of(card.citation_key, expected)
        if position is None:
            continue
        paper_id = selected.items[position].paper.paper_id
        if card.analysis.paper_id != paper_id:
            violations.append(
                Violation(
                    kind="missing_reference",
                    severity=SEVERITY,
                    location=f"paper_cards[{index}].analysis.paper_id",
                    detail=(
                        f"{card.citation_key} maps to {paper_id!r} but the analysis "
                        f"is for {card.analysis.paper_id!r}"
                    ),
                )
            )
    return violations


def _check_comparison(briefing: Briefing, expected: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    rows = briefing.comparison
    allowed = set(expected)

    if len(rows) != len(expected):
        violations.append(
            Violation(
                kind="length_limit",
                severity=SEVERITY,
                location="comparison",
                detail=f"expected {len(expected)} rows, found {len(rows)}",
            )
        )

    keys = [row.citation_key for row in rows]
    for index, key in enumerate(keys):
        if key not in allowed:
            violations.append(
                Violation(
                    kind="unknown_citation_key",
                    severity=SEVERITY,
                    location=f"comparison[{index}].citation_key",
                    detail=f"{key!r} is not one of {', '.join(expected)}",
                )
            )
    for key, count in sorted(Counter(keys).items()):
        if count > 1:
            violations.append(
                Violation(
                    kind="duplicate_reference",
                    severity=SEVERITY,
                    location="comparison",
                    detail=f"{key} appears {count} times",
                )
            )
    for key in expected:
        if key not in keys:
            violations.append(
                Violation(
                    kind="missing_reference",
                    severity=SEVERITY,
                    location="comparison",
                    detail=f"no comparison row for {key}",
                )
            )
    return violations


def _check_claims(briefing: Briefing, expected: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    allowed = set(expected)
    sections = (
        ("gaps_and_open_questions", briefing.gaps_and_open_questions),
        ("further_reading", briefing.further_reading),
    )
    for section, claims in sections:
        for index, claim in enumerate(claims):
            for key in claim.citation_keys:
                if key not in allowed:
                    violations.append(
                        Violation(
                            kind="unknown_citation_key",
                            severity=SEVERITY,
                            location=f"{section}[{index}].citation_keys",
                            detail=f"{key!r} is not one of {', '.join(expected)}",
                        )
                    )
    return violations


def _check_summary(briefing: Briefing, limit: int) -> list[Violation]:
    length = len(briefing.executive_summary)
    if length <= limit:
        return []
    return [
        Violation(
            kind="length_limit",
            severity=SEVERITY,
            location="executive_summary",
            detail=f"{length} characters, limit is {limit}",
        )
    ]


def _check_language(
    briefing: Briefing,
    expected_lang: Literal["zh", "en"] | None,
) -> list[Violation]:
    violations: list[Violation] = []
    if expected_lang is not None and briefing.lang != expected_lang:
        violations.append(
            Violation(
                kind="language_mismatch",
                severity=SEVERITY,
                location="lang",
                detail=f"briefing is {briefing.lang!r} but the run expects {expected_lang!r}",
            )
        )

    summary = briefing.executive_summary
    if briefing.lang == "zh" and not contains_cjk(summary):
        violations.append(
            Violation(
                kind="language_mismatch",
                severity=SEVERITY,
                location="executive_summary",
                detail="a Chinese briefing must contain Chinese text",
            )
        )
    if briefing.lang == "en" and not contains_latin(summary):
        violations.append(
            Violation(
                kind="language_mismatch",
                severity=SEVERITY,
                location="executive_summary",
                detail="an English briefing must contain Latin text",
            )
        )
    return violations


def _check_evidence(
    briefing: Briefing,
    selected: SelectedPapers,
    expected: list[str],
) -> list[Violation]:
    """Quotes must still be traceable to the paper their key points at."""
    violations: list[Violation] = []
    for index, card in enumerate(briefing.paper_cards):
        position = _position_of(card.citation_key, expected)
        if position is None:
            continue
        paper = selected.items[position].paper
        haystack = build_haystack(paper)
        for evidence_index, evidence in enumerate(card.analysis.evidence):
            if not quote_is_supported(evidence.quote, haystack):
                violations.append(
                    Violation(
                        kind="unsupported_claim",
                        severity=SEVERITY,
                        location=f"paper_cards[{index}].analysis.evidence[{evidence_index}]",
                        detail=(
                            f"quote {evidence.quote[:60]!r} does not appear in {paper.paper_id}"
                        ),
                    )
                )
    return violations


def _position_of(key: str, expected: list[str]) -> int | None:
    """Map ``Pk`` to its zero-based position, or ``None`` if it is not valid."""
    if key not in expected:
        return None
    return int(key[1:]) - 1

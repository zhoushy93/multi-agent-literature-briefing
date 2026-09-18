"""Assembling the two verification layers and deciding what happens next.

This module is deliberately free of LLM imports so an L1-only run can use it.
The outcome vocabulary matches docs/architecture.md §2.2 and §5:

* ``verified``            — no fatal finding; render the briefing.
* ``revise``              — fatal findings and the one revision has not been
                            spent yet; ask the synthesizer to repair them.
* ``verification_failed`` — fatal findings after the revision was spent; the
                            run ends and the artifacts stay on disk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from briefing.schemas import VerificationReport, Violation

Outcome = Literal["verified", "revise", "verification_failed"]


def has_fatal(violations: Sequence[Violation]) -> bool:
    return any(violation.severity == "fatal" for violation in violations)


def resolve_outcome(report: VerificationReport) -> Outcome:
    """Map a report to the next action, given whether a revision was spent."""
    if report.passed:
        return "verified"
    if report.revision_requested:
        return "revise"
    return "verification_failed"


def build_report(
    deterministic: Sequence[Violation] = (),
    semantic: Sequence[Violation] = (),
    *,
    revision_used: bool = False,
) -> VerificationReport:
    """Combine both layers. ``warn`` findings never block a render."""
    deterministic_findings = list(deterministic)
    semantic_findings = list(semantic)
    fatal = has_fatal(deterministic_findings) or has_fatal(semantic_findings)
    return VerificationReport(
        passed=not fatal,
        deterministic=deterministic_findings,
        semantic=semantic_findings,
        revision_requested=bool(fatal and not revision_used),
    )


def fatal_findings(report: VerificationReport) -> list[Violation]:
    """The findings worth sending back to the synthesizer."""
    return [
        violation
        for violation in (*report.deterministic, *report.semantic)
        if violation.severity == "fatal"
    ]

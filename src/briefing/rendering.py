"""Shared rendering of the paper catalogue.

Both the synthesizer (which writes the briefing) and the semantic verifier
(which audits it) must see the *same* material. Rendering it in one place means
the audit can never drift from the text the briefing was written from.

This module depends on ``briefing.schemas`` only, so the deterministic
verification layer can import it without pulling in LLM code.
"""

from __future__ import annotations

from collections.abc import Sequence

from briefing.schemas import PaperAnalysis, SelectedPapers


def render_catalogue(
    selected: SelectedPapers,
    analyses: Sequence[PaperAnalysis],
    keys: Sequence[str],
    *,
    include_abstract: bool = False,
) -> str:
    """Render each selected paper and its analysis under its citation key.

    ``include_abstract`` is on for the semantic verifier, which has to judge
    whether a claim is supported by the paper, and off for the synthesizer,
    which works from the analyses and would otherwise pay for every abstract.
    """
    blocks = ["Allowed citation keys: " + ", ".join(keys)]
    for key, screened, analysis in zip(keys, selected.items, analyses, strict=True):
        paper = screened.paper
        year = paper.year if paper.year is not None else "n.d."
        lines = [
            f"[{key}] {paper.title}",
            # The Briefing schema requires each card's analysis to carry this
            # id, so the catalogue has to show it; otherwise the model would
            # have to invent one and every briefing would fail verification.
            f"  paper_id: {paper.paper_id}",
            f"  Authors: {', '.join(paper.authors)}",
            f"  Year: {year} | Venue: {paper.venue or 'unknown venue'}",
            f"  Problem: {analysis.problem}",
            f"  Method: {analysis.method}",
            f"  Data and experiments: {analysis.data_and_experiments}",
            f"  Key findings: {'; '.join(analysis.key_findings)}",
            f"  Limitations: {'; '.join(analysis.limitations)}",
            f"  Reusable ideas: {'; '.join(analysis.reusable_ideas) or 'none'}",
            f"  Confidence: {analysis.confidence}",
        ]
        if include_abstract:
            lines.append(f"  Abstract: {paper.abstract or 'not available'}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)

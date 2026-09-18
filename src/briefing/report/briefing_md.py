"""Markdown projection of a verified briefing.

``briefing.md`` is the editable counterpart of ``report.pdf``. Both are
rendered from the same validated ``Briefing`` model, which is what makes
"the PDF and the Markdown agree" true by construction rather than by
inspection.
"""

from __future__ import annotations

from briefing.report.references import (
    Reference,
    build_reference_map,
    resolve_citations,
    resolve_claim_keys,
)
from briefing.schemas import Briefing, SearchPlan, SelectedPapers


def render_briefing_markdown(
    briefing: Briefing,
    selected: SelectedPapers,
    references: list[Reference],
    *,
    search_plan: SearchPlan | None = None,
    model_id: str = "unknown",
    prompt_versions: dict[str, str] | None = None,
) -> str:
    """Render the ten chapters of AGENTS.md §8 as Markdown."""
    mapping = build_reference_map(selected)
    lines: list[str] = [f"# {briefing.topic}", ""]

    lines += ["## Executive summary", "", briefing.executive_summary, ""]

    # Mirrors the PDF's "topic overview" chapter.
    lines += ["## Topic overview", ""]
    lines += [
        "| Ref | Paper | Method family | Headline finding | Relevance |",
        "| --- | --- | --- | --- | --- |",
    ]
    for position, (card, screened) in enumerate(
        zip(briefing.paper_cards, selected.items, strict=True),
        start=1,
    ):
        number = mapping.get(card.citation_key, position)
        family = next(
            (
                row.method_family
                for row in briefing.comparison
                if row.citation_key == card.citation_key
            ),
            "",
        )
        lines.append(
            f"| [{number}] | {screened.paper.title} | {family} "
            f"| {card.analysis.key_findings[0]} | {card.analysis.relevance} |"
        )
    lines.append("")

    lines += ["## Background", "", resolve_citations(briefing.background_md, mapping), ""]
    lines += ["## Method", "", resolve_citations(briefing.method_md, mapping), ""]

    if search_plan is not None:
        lines += ["### Queries", ""]
        lines += [f"- `{query.source}: {query.q}`" for query in search_plan.queries]
        lines.append("")
        lines += ["### Inclusion criteria", ""]
        lines += [f"- {item}" for item in search_plan.inclusion_criteria]
        lines.append("")
        if search_plan.exclusion_criteria:
            lines += ["### Exclusion criteria", ""]
            lines += [f"- {item}" for item in search_plan.exclusion_criteria]
            lines.append("")

    lines += ["## Paper by paper", ""]
    for position, (card, screened) in enumerate(
        zip(briefing.paper_cards, selected.items, strict=True),
        start=1,
    ):
        paper = screened.paper
        number = mapping.get(card.citation_key, position)
        year = paper.year if paper.year is not None else "n.d."
        lines += [
            f"### [{number}] {paper.title}",
            "",
            f"- Authors: {', '.join(paper.authors)}",
            f"- Year: {year}",
            f"- Venue: {paper.venue or paper.origin}",
            f"- Link: {paper.url}",
            "",
            f"**Problem.** {card.analysis.problem}",
            "",
            f"**Method.** {card.analysis.method}",
            "",
            f"**Data and experiments.** {card.analysis.data_and_experiments}",
            "",
            "**Key findings**",
            "",
            *[f"- {finding}" for finding in card.analysis.key_findings],
            "",
            "**Limitations**",
            "",
            *[f"- {limitation}" for limitation in card.analysis.limitations],
            "",
            f"**Relevance to the topic.** {card.analysis.relevance}",
            "",
        ]
        if card.analysis.reusable_ideas:
            lines += ["**Reusable ideas**", ""]
            lines += [f"- {idea}" for idea in card.analysis.reusable_ideas]
            lines.append("")

    lines += ["## Comparison", ""]
    lines += [
        "| Ref | Task | Method | Data | Metrics | Main result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for position, row in enumerate(briefing.comparison, start=1):
        number = mapping.get(row.citation_key, position)
        lines.append(
            f"| [{number}] | {row.task} | {row.method_family} | {row.data} "
            f"| {row.metrics} | {row.main_result} |"
        )
    lines.append("")

    lines += ["## Gaps and open questions", ""]
    for claim in briefing.gaps_and_open_questions:
        citations = resolve_claim_keys(claim.citation_keys, mapping)
        lines.append(f"- {claim.text} {citations}")
    lines.append("")

    if briefing.further_reading:
        lines += ["## Further reading", ""]
        for claim in briefing.further_reading:
            citations = resolve_claim_keys(claim.citation_keys, mapping)
            lines.append(f"- {claim.text} {citations}")
        lines.append("")

    lines += ["## References", ""]
    for reference in references:
        lines.append(f"{reference.index}. {reference.text}")
    lines.append("")

    lines += ["## Appendix: generation parameters", ""]
    lines += [
        f"- Run id: {briefing.run_id}",
        f"- Model: {model_id}",
        f"- Language: {briefing.lang}",
    ]
    for name, version in sorted((prompt_versions or {}).items()):
        lines.append(f"- Prompt {name}: {version}")
    lines.append("")
    return "\n".join(lines)

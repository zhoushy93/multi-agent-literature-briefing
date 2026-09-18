"""Reference numbering tests: deterministic, driven by selection order."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from briefing.report.references import (
    build_reference_map,
    build_references,
    format_authors,
    format_reference,
    resolve_citations,
    resolve_claim_keys,
)
from briefing.schemas import Paper, ScreenedPaper, SelectedPapers

RETRIEVED_AT = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def make_paper(paper_id: str, **overrides: Any) -> Paper:
    data: dict[str, Any] = {
        "paper_id": paper_id,
        "title": f"Study about {paper_id}",
        "authors": ["Alice Author", "Bob Builder"],
        "year": 2024,
        "venue": "Journal of Weather ML",
        "origin": "arxiv",
        "url": f"https://arxiv.org/abs/{paper_id}",
        "arxiv_id": paper_id,
        "abstract": "We study diffusion models.",
        "retrieved_at": RETRIEVED_AT,
    }
    data.update(overrides)
    return Paper(**data)


def make_selected(paper_ids: list[str] | None = None, **overrides: Any) -> SelectedPapers:
    ids = paper_ids or ["2401.00001", "2401.00002", "2401.00003"]
    return SelectedPapers(
        items=[
            ScreenedPaper(
                paper=make_paper(paper_id, **overrides),
                rank=index,
                relevance_score=0.9,
                rationale="relevant",
            )
            for index, paper_id in enumerate(ids, start=1)
        ]
    )


# --- numbering ---------------------------------------------------------------


def test_numbering_follows_selected_order() -> None:
    mapping = build_reference_map(make_selected())
    assert mapping == {"P1": 1, "P2": 2, "P3": 3}


def test_numbering_is_deterministic() -> None:
    selected = make_selected()
    assert build_reference_map(selected) == build_reference_map(selected)


def test_numbering_ignores_the_paper_id_format() -> None:
    """Numbers come from position, so odd ids cannot shift the numbering."""
    mapping = build_reference_map(make_selected(["doi:10.1/x", "arxiv:9999.1", "weird:id"]))
    assert mapping == {"P1": 1, "P2": 2, "P3": 3}


def test_the_map_and_the_list_agree() -> None:
    selected = make_selected()
    references = build_references(selected)
    assert [reference.index for reference in references] == [1, 2, 3]
    assert [reference.key for reference in references] == ["P1", "P2", "P3"]
    assert build_reference_map(selected) == {
        reference.key: reference.index for reference in references
    }


def test_references_keep_selection_order() -> None:
    selected = make_selected(["2401.00003", "2401.00001", "2401.00002"])
    titles = [reference.paper.paper_id for reference in build_references(selected)]
    assert titles == ["2401.00003", "2401.00001", "2401.00002"]


# --- formatting --------------------------------------------------------------


def test_reference_format_follows_the_house_style() -> None:
    paper = make_paper("2401.00001", doi="10.1234/jwm.2024.345")
    assert format_reference(paper) == (
        "Alice Author, Bob Builder. Study about 2401.00001. "
        "Journal of Weather ML, 2024. https://doi.org/10.1234/jwm.2024.345"
    )


def test_reference_falls_back_to_the_landing_page() -> None:
    paper = make_paper("2401.00001", doi=None)
    assert format_reference(paper).endswith("https://arxiv.org/abs/2401.00001")


def test_many_authors_are_truncated() -> None:
    assert format_authors(["A", "B", "C", "D", "E"]) == "A, B, C, et al."
    assert format_authors(["A", "B"]) == "A, B"


def test_missing_year_and_venue_are_labelled() -> None:
    paper = make_paper("2401.00001", year=None, venue=None)
    text = format_reference(paper)
    assert "arxiv, n.d." in text, "the origin stands in when there is no venue"


def test_reference_uses_only_retrieved_metadata() -> None:
    """The text is built from the Paper fields, nothing else."""
    paper = make_paper("2401.00001", title="A specific title", doi="10.1/abc")
    text = format_reference(paper)
    assert "A specific title" in text
    assert "10.1/abc" in text


# --- inline citations --------------------------------------------------------


def test_inline_citations_are_resolved() -> None:
    mapping = {"P1": 1, "P2": 2}
    assert resolve_citations("As shown in [P1] and (P2).", mapping) == ("As shown in [1] and [2].")


def test_bare_keys_are_resolved_too() -> None:
    assert resolve_citations("See P2 for details.", {"P2": 2}) == "See [2] for details."


def test_unknown_inline_key_is_left_alone() -> None:
    assert resolve_citations("See [P9].", {"P1": 1}) == "See [P9]."


def test_lookalike_tokens_are_not_touched() -> None:
    mapping = {"P1": 1}
    assert resolve_citations("TOP1 and P53 and XP1", mapping) == "TOP1 and P53 and XP1"


def test_claim_keys_render_as_a_citation_list() -> None:
    assert resolve_claim_keys(["P1", "P3"], {"P1": 1, "P3": 3}) == "[1], [3]"


def test_claim_keys_ignore_unmapped_keys() -> None:
    assert resolve_claim_keys(["P1", "P9"], {"P1": 1}) == "[1]"

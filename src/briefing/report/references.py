"""Deterministic citation numbering (docs/architecture.md §3.3).

The model writes citation keys (``P1``..``Pn``). This module turns them into
the ``[1]``..``[n]`` numbers a reader sees, and builds the reference list from
the retrieved metadata alone. Because the numbering is derived from
``SelectedPapers`` order in code, a citation cannot point at a paper that was
not selected, and two different papers can never share a number.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

from briefing.schemas import Paper, SelectedPapers, citation_keys

# More than this and the entry stops being readable in a table of references.
MAX_LISTED_AUTHORS = 3


@dataclass(frozen=True)
class Reference:
    """One numbered reference, in the order it appears in the briefing."""

    index: int
    key: str
    paper: Paper
    text: str


def build_reference_map(selected: SelectedPapers) -> dict[str, int]:
    """``Pk -> k``, assigned in ``SelectedPapers`` order."""
    return {
        key: position
        for position, key in enumerate(
            citation_keys(len(selected.items)),
            start=1,
        )
    }


def format_authors(authors: list[str]) -> str:
    if not authors:
        return "Unknown authors"
    if len(authors) <= MAX_LISTED_AUTHORS:
        return ", ".join(authors)
    listed = ", ".join(authors[:MAX_LISTED_AUTHORS])
    return f"{listed}, et al."


def format_reference(paper: Paper) -> str:
    """AGENTS.md §8: ``作者. 标题. 来源, 年份. DOI/URL``."""
    venue = paper.venue or paper.origin
    year = str(paper.year) if paper.year is not None else "n.d."
    identifier = f"https://doi.org/{paper.doi}" if paper.doi else paper.url
    return f"{format_authors(paper.authors)}. {paper.title}. {venue}, {year}. {identifier}"


def build_references(selected: SelectedPapers) -> list[Reference]:
    """The reference list, generated only from retrieved metadata."""
    keys = citation_keys(len(selected.items))
    return [
        Reference(
            index=index,
            key=key,
            paper=screened.paper,
            text=format_reference(screened.paper),
        )
        for index, (key, screened) in enumerate(
            zip(keys, selected.items, strict=True),
            start=1,
        )
    ]


_CITATION = re.compile(r"[\[(]?\b(P\d+)\b[\])]?")


def resolve_citations(text: str, mapping: Mapping[str, int]) -> str:
    """Rewrite inline ``Pk`` markers in prose as ``[k]``.

    Accepts ``[P1]``, ``(P1)`` and bare ``P1``, because the prose comes from a
    language model and the marker style is not worth a retry. A key that is not
    in the map is left exactly as it was: the verifier already rejected unknown
    keys, so anything left here is ordinary text.
    """

    def replace(match: re.Match[str]) -> str:
        index = mapping.get(match.group(1))
        return f"[{index}]" if index is not None else match.group(0)

    return _CITATION.sub(replace, text)


def resolve_claim_keys(keys: list[str], mapping: Mapping[str, int]) -> str:
    """Render a claim's keys as ``[1], [3]``."""
    return ", ".join(f"[{mapping[key]}]" for key in keys if key in mapping)

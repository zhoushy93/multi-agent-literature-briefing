"""Quote provenance rules, shared by the analyzer and the verifier.

These live outside ``briefing.agents`` on purpose: the deterministic verifier
(docs/architecture.md §4, S7 L1) must be able to re-prove that every quote in
a briefing still appears in the paper it claims to come from, without pulling
any LLM code into its import graph.
"""

from __future__ import annotations

from briefing.schemas import Paper

ELLIPSES = ("...", "…", ". . .")

_CJK_START = "\u3400"
_CJK_END = "\u9fff"


def normalize_text(text: str) -> str:
    """Case-fold and collapse whitespace so formatting is not a mismatch."""
    return " ".join(text.casefold().split())


def split_ellipsis(quote: str) -> list[str]:
    """Split a quote on ellipses, returning normalised non-empty fragments."""
    fragments = [quote]
    for marker in ELLIPSES:
        fragments = [piece for fragment in fragments for piece in fragment.split(marker)]
    return [piece for piece in (normalize_text(fragment) for fragment in fragments) if piece]


def build_haystack(paper: Paper) -> str:
    """The text a quote is allowed to come from: abstract plus metadata."""
    parts = [
        paper.title,
        ", ".join(paper.authors),
        paper.venue or "",
        str(paper.year) if paper.year is not None else "",
        paper.abstract,
    ]
    return " ".join(part for part in parts if part.strip())


def quote_is_supported(quote: str, haystack: str) -> bool:
    """True when every fragment of ``quote`` appears, in order, in ``haystack``.

    Matching ignores case and whitespace runs, and tolerates ``...`` used to
    elide words inside a single quote. It does not tolerate paraphrasing.
    """
    fragments = split_ellipsis(quote)
    if not fragments:
        return False
    normalized = normalize_text(haystack)
    position = 0
    for fragment in fragments:
        found = normalized.find(fragment, position)
        if found < 0:
            return False
        position = found + len(fragment)
    return True


def contains_cjk(text: str) -> bool:
    """True when the text contains at least one CJK ideograph."""
    return any(_CJK_START <= character <= _CJK_END for character in text)


def contains_latin(text: str) -> bool:
    """True when the text contains at least one ASCII letter."""
    return any(character.isascii() and character.isalpha() for character in text)

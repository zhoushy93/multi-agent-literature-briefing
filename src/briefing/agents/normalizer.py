"""S3 Normalizer: deduplication and pre-filtering.

Deterministic by construction — no LLM, no IO, no clock. Two runs over the same
input produce byte-identical output, which is what makes stage checkpoints
(``_stages/03_deduped.json``) and ``--resume`` trustworthy.

Identity priority is DOI -> arXiv ID -> normalised title. The title is a
*fallback* identity: two records that both carry a DOI may only merge when the
DOIs agree, otherwise a journal paper and a preprint of a different paper with
a colliding title would be fused.

``no_abstract`` and ``short_abstract`` are recorded in the ``dropped`` ledger
but the papers are **kept** (docs/architecture.md §4, S3: "降权保留", "打
short_abstract 标记"). The ledger is a pre-filter disposition log, not a list
of deletions; the Screener (S4) reads it to rank those candidates lower.
"""

from __future__ import annotations

from collections.abc import Sequence

from briefing.schemas import CandidateList, DedupedCandidates, Paper

# AGENTS.md §7: candidates with a shorter abstract are down-weighted.
MIN_ABSTRACT_CHARS = 300

# AGENTS.md §7: fetch 20-40 candidates, then screen. A pool this size keeps the
# screener's prompt affordable and short enough that the reply is not truncated.
DEFAULT_MAX_CANDIDATES = 40

# docs/architecture.md §4, S3: output order is by source priority first.
SOURCE_PRIORITY: dict[str, int] = {
    "arxiv": 0,
    "openalex": 1,
    "crossref": 2,
    "semantic_scholar": 3,
}
UNKNOWN_SOURCE_PRIORITY = 99


def normalize_doi(doi: str) -> str:
    """Lower-case a DOI and drop any URL or ``doi:`` decoration."""
    value = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.strip()


def normalize_arxiv_id(arxiv_id: str) -> str:
    """Drop the ``arxiv:`` prefix and any version suffix."""
    value = arxiv_id.strip().lower()
    if value.startswith("arxiv:"):
        value = value[len("arxiv:") :]
    head, separator, tail = value.rpartition("v")
    if head and separator and tail.isdigit():
        value = head
    return value


def normalize_title(title: str) -> str:
    """Lower-case, drop the subtitle, strip punctuation, collapse whitespace."""
    primary = title.split(":", 1)[0].strip() or title
    flattened = "".join(
        character if character.isalnum() or character.isspace() else " "
        for character in primary.lower()
    )
    return " ".join(flattened.split())


def identity_keys(paper: Paper) -> list[tuple[str, str]]:
    """Identity keys in priority order."""
    keys: list[tuple[str, str]] = []
    if paper.doi:
        keys.append(("doi", normalize_doi(paper.doi)))
    if paper.arxiv_id:
        keys.append(("arxiv", normalize_arxiv_id(paper.arxiv_id)))
    title_key = normalize_title(paper.title)
    if title_key:
        keys.append(("title", title_key))
    return keys


class _UnionFind:
    """Disjoint sets, so a chain of records still collapses into one group."""

    def __init__(self) -> None:
        self._parent: list[int] = []

    def add(self) -> int:
        self._parent.append(len(self._parent))
        return len(self._parent) - 1

    def find(self, node: int) -> int:
        root = node
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[node] != root:
            self._parent[node], node = root, self._parent[node]
        return root

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def _richness(paper: Paper) -> tuple[int, int, int, int]:
    """How much usable metadata a record carries; higher wins."""
    return (
        int(paper.doi is not None),
        int(paper.arxiv_id is not None),
        len(paper.abstract),
        int(paper.year is not None),
    )


def _choose_primary(group: Sequence[Paper]) -> Paper:
    """Richest record wins; the earliest one wins ties (deterministic)."""
    best = 0
    for index in range(1, len(group)):
        if _richness(group[index]) > _richness(group[best]):
            best = index
    return group[best]


def deduplicate(papers: Sequence[Paper]) -> tuple[list[Paper], int]:
    """Collapse duplicates, returning the survivors and how many were merged."""
    union_find = _UnionFind()
    first_seen: dict[tuple[str, str], int] = {}

    for index, paper in enumerate(papers):
        union_find.add()
        for kind, value in identity_keys(paper):
            key = (kind, value)
            other_index = first_seen.get(key)
            if other_index is None:
                first_seen[key] = index
                continue
            if kind == "title" and not _titles_may_merge(paper, papers[other_index]):
                continue
            union_find.union(index, other_index)

    groups: dict[int, list[Paper]] = {}
    for index, paper in enumerate(papers):
        groups.setdefault(union_find.find(index), []).append(paper)

    survivors = [_choose_primary(group) for group in groups.values()]
    return survivors, len(papers) - len(survivors)


def _titles_may_merge(left: Paper, right: Paper) -> bool:
    """A shared title only merges records that do not disagree on a stronger id."""
    if not left.doi or not right.doi:
        return True
    return normalize_doi(left.doi) == normalize_doi(right.doi)


def sort_key(paper: Paper) -> tuple[int, int, str]:
    """(source priority, year desc, paper_id) — docs/architecture.md §4, S3."""
    return (
        SOURCE_PRIORITY.get(paper.origin, UNKNOWN_SOURCE_PRIORITY),
        -(paper.year or 0),
        paper.paper_id,
    )


def normalize(
    candidates: CandidateList,
    *,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> DedupedCandidates:
    """Deduplicate, cap the pool, log dispositions, and order the survivors."""
    survivors, duplicates_removed = deduplicate(candidates.papers)

    ledger = dict(candidates.dropped)
    for paper in survivors:
        abstract = paper.abstract.strip()
        if not abstract:
            reason = "no_abstract"
        elif len(abstract) < MIN_ABSTRACT_CHARS:
            reason = "short_abstract"
        else:
            continue
        ledger[reason] = ledger.get(reason, 0) + 1

    ordered = sorted(survivors, key=sort_key)
    if len(ordered) > max_candidates:
        # Retrieval can easily return a hundred papers. Sending all of them to
        # the screener makes the prompt expensive and its reply liable to be
        # truncated, so the pool is capped and the loss is recorded.
        ledger["pool_capped"] = ledger.get("pool_capped", 0) + (len(ordered) - max_candidates)
        ordered = ordered[:max_candidates]

    return DedupedCandidates(
        papers=ordered,
        duplicates_removed=duplicates_removed,
        dropped=ledger,
        queries_used=list(candidates.queries_used),
    )


class Normalizer:
    """S3 as an agent: pure today, async so the orchestrator can await it."""

    name = "normalizer"

    def __init__(self, *, max_candidates: int = DEFAULT_MAX_CANDIDATES) -> None:
        self._max_candidates = max_candidates

    async def run(self, candidates: CandidateList) -> DedupedCandidates:
        return normalize(candidates, max_candidates=self._max_candidates)

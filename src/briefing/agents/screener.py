"""S4 Screener: pick 3-5 papers from the deduplicated candidates.

The model only ever sees short tags (``C1``..``Cn``) and only ever returns
tags, so it cannot corrupt metadata or smuggle in a paper that was not
retrieved. Everything else — resolving tags, dropping unknown or repeated
tags, renumbering ranks, enforcing the 3..5 contract — is deterministic code.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence

from briefing.agents.normalizer import MIN_ABSTRACT_CHARS
from briefing.errors import InsufficientPapersError
from briefing.llm.base import (
    DEFAULT_MAX_TOKENS,
    CompletionRequest,
    LLMClient,
    complete_structured,
)
from briefing.prompts.loader import PromptLoader
from briefing.schemas import (
    MAX_PAPERS,
    MIN_PAPERS,
    DedupedCandidates,
    Paper,
    ScreenedPaper,
    ScreenerChoice,
    ScreenerResponse,
    SearchPlan,
    SelectedPapers,
    Violation,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "screener_v1"
STAGE = "S4"
TEMPERATURE = 0.0
TAG_PREFIX = "C"

SYSTEM = "You are a paper screening agent. You reply with a single JSON object and nothing else."

INSUFFICIENT_SUGGESTIONS: tuple[str, ...] = (
    "widen the topic wording or add synonyms",
    "extend or drop the time window",
    "enable an additional source such as OpenAlex or Crossref",
    "raise the candidate limit so more papers reach screening",
)


class Screener:
    """S4: produces ``SelectedPapers`` or fails with a readable reason."""

    name = "screener"

    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: PromptLoader | None = None,
        min_papers: int = MIN_PAPERS,
        max_papers: int = MAX_PAPERS,
        temperature: float = TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if min_papers > max_papers:
            raise ValueError("min_papers must be <= max_papers")
        if min_papers < MIN_PAPERS or max_papers > MAX_PAPERS:
            # SelectedPapers itself is bounded to 3..5 (AGENTS.md §11), so a
            # looser Screener could never produce a valid result. Fail at
            # construction instead of deep inside a run.
            raise ValueError(
                f"min_papers/max_papers must stay within {MIN_PAPERS}..{MAX_PAPERS}, "
                f"got {min_papers}..{max_papers}"
            )
        self._llm = llm
        self._prompts = prompts or PromptLoader()
        self._min_papers = min_papers
        self._max_papers = max_papers
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Findings from the last run, relayed into manifest.json by the
        # orchestrator (Step 13).
        self.warnings: list[Violation] = []

    async def run(self, candidates: DedupedCandidates, plan: SearchPlan) -> SelectedPapers:
        self.warnings = []
        tagged = list(self._tag_candidates(candidates.papers))
        if not tagged:
            raise self._insufficient(found=0, candidate_count=0, candidates=candidates)

        prompt = self._prompts.load(PROMPT_NAME)
        user = prompt.render(
            topic=plan.normalized_topic,
            criteria=self._render_criteria(plan),
            min_papers=str(self._min_papers),
            max_papers=str(self._max_papers),
            candidates="\n".join(self._render_candidate(tag, paper) for tag, paper in tagged),
        )
        response = await complete_structured(
            self._llm,
            CompletionRequest(
                system=SYSTEM,
                user=user,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stage=STAGE,
                prompt_version=prompt.version,
            ),
            ScreenerResponse,
        )
        return self._select(response, tagged, candidates)

    # --- rendering ----------------------------------------------------------

    def _tag_candidates(self, papers: Sequence[Paper]) -> Iterator[tuple[str, Paper]]:
        for index, paper in enumerate(papers, start=1):
            yield f"{TAG_PREFIX}{index}", paper

    def _render_criteria(self, plan: SearchPlan) -> str:
        lines: list[str] = []
        for criterion in plan.inclusion_criteria:
            lines.append(f"- include: {criterion}")
        for criterion in plan.exclusion_criteria:
            lines.append(f"- exclude: {criterion}")
        return "\n".join(lines)

    def _render_candidate(self, tag: str, paper: Paper) -> str:
        authors = ", ".join(paper.authors[:3]) or "unknown authors"
        year = paper.year if paper.year is not None else "n.d."
        venue = paper.venue or "unknown venue"
        abstract = paper.abstract.strip()
        marker = " [short-abstract]" if len(abstract) < MIN_ABSTRACT_CHARS else ""
        return (
            f"{tag}{marker}: {paper.title} ({authors}, {year}, {venue})\n"
            f"    {abstract if abstract else 'no abstract available'}"
        )

    # --- selection ----------------------------------------------------------

    def _select(
        self,
        response: ScreenerResponse,
        tagged: Sequence[tuple[str, Paper]],
        candidates: DedupedCandidates,
    ) -> SelectedPapers:
        by_tag = dict(tagged)
        chosen: list[tuple[int, int, ScreenerChoice, Paper]] = []
        seen: set[str] = set()

        for order, choice in enumerate(response.selected):
            paper = by_tag.get(choice.tag)
            if paper is None:
                self._warn(
                    Violation(
                        kind="unknown_candidate",
                        severity="warn",
                        location=f"selected[{order}]",
                        detail=f"model selected {choice.tag!r}, which is not a candidate tag",
                    )
                )
                continue
            if choice.tag in seen:
                self._warn(
                    Violation(
                        kind="duplicate_reference",
                        severity="warn",
                        location=f"selected[{order}]",
                        detail=f"candidate {choice.tag} was selected more than once",
                    )
                )
                continue
            seen.add(choice.tag)
            chosen.append((choice.rank, order, choice, paper))

        chosen.sort(key=lambda item: (item[0], item[1]))
        if len(chosen) > self._max_papers:
            self._warn(
                Violation(
                    kind="length_limit",
                    severity="warn",
                    location="selected",
                    detail=(
                        f"{len(chosen)} candidates were selected; "
                        f"keeping the top {self._max_papers}"
                    ),
                )
            )
            chosen = chosen[: self._max_papers]

        if len(chosen) < self._min_papers:
            raise self._insufficient(
                found=len(chosen),
                candidate_count=len(candidates.papers),
                candidates=candidates,
            )

        items = [
            ScreenedPaper(
                paper=paper,
                rank=index,
                relevance_score=choice.relevance_score,
                rationale=choice.rationale,
                matched_inclusion=choice.matched_inclusion,
                matched_exclusion=choice.matched_exclusion,
            )
            for index, (_, _, choice, paper) in enumerate(chosen, start=1)
        ]
        return SelectedPapers(items=items, selection_notes=response.selection_notes)

    # --- helpers ------------------------------------------------------------

    def _warn(self, violation: Violation) -> None:
        self.warnings.append(violation)
        logger.warning(
            "screener warning",
            extra={"detail": violation.detail, "location": violation.location},
        )

    def _insufficient(
        self,
        *,
        found: int,
        candidate_count: int,
        candidates: DedupedCandidates,
    ) -> InsufficientPapersError:
        return InsufficientPapersError(
            found=found,
            required=self._min_papers,
            candidate_count=candidate_count,
            queries=candidates.queries_used,
            suggestions=INSUFFICIENT_SUGGESTIONS,
        )

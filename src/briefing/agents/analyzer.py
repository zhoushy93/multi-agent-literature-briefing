"""S5 Analyzer: one call per paper, run in parallel.

Two rules make this stage trustworthy, and both are enforced in code rather
than requested in prose:

* every ``evidence.quote`` must appear in the material the model was given, so
  a briefing can always be traced back to retrieved text;
* the returned ``paper_id`` must equal the one that was sent, so analyses can
  never be attached to the wrong paper.

A paper that fails either rule is retried at most twice and then fails the
whole run (AGENTS.md §1.1). Dropping the paper would produce a briefing that
claims coverage it does not have.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from briefing.errors import AnalysisFailedError
from briefing.evidence import build_haystack, quote_is_supported
from briefing.llm.base import (
    DEFAULT_MAX_TOKENS,
    CompletionRequest,
    LLMClient,
    complete_structured,
)
from briefing.prompts.loader import Prompt, PromptLoader
from briefing.schemas import MAX_QUOTE_CHARS, Paper, PaperAnalysis, ScreenedPaper

logger = logging.getLogger(__name__)

PROMPT_NAME = "analyzer_v2"
STAGE = "S5"

# AGENTS.md §6: analysis runs slightly warm, extraction stays at zero.
TEMPERATURE = 0.2

# docs/architecture.md §4, S5: never feed a whole paper, and cap the call.
DEFAULT_MAX_INPUT_CHARS = 8000

TRUNCATION_MARKER = "...[truncated]"

# How far back to look for a word boundary when shortening a quote.
_BOUNDARY_WINDOW = 40


def shorten_quote(quote: str) -> str:
    """Trim an over-long quote to ``MAX_QUOTE_CHARS``, keeping it verbatim.

    The result is still a prefix of the original span, so it is still
    checkable; the trailing ellipsis is the usual mark for a cut passage and
    the provenance matcher already understands it.
    """
    if len(quote) <= MAX_QUOTE_CHARS:
        return quote
    # Reserve room for the ellipsis so the result still fits the limit.
    cut = quote[: MAX_QUOTE_CHARS - len(" …")]
    boundary = cut.rfind(" ", max(0, len(cut) - _BOUNDARY_WINDOW))
    if boundary > 0:
        cut = cut[:boundary]
    return f"{cut.rstrip()} …"


class Analyzer:
    """S5 for a single paper: ``ScreenedPaper`` in, ``PaperAnalysis`` out."""

    name = "analyzer"

    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: PromptLoader | None = None,
        topic: str = "",
        lang: str = "en",
        temperature: float = TEMPERATURE,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._llm = llm
        self._prompts = prompts or PromptLoader()
        self._topic = topic
        self._lang = lang
        self._temperature = temperature
        self._max_input_chars = max_input_chars
        self._max_tokens = max_tokens
        self.truncations = 0
        self.shortened_quotes = 0

    async def run(self, screened: ScreenedPaper) -> PaperAnalysis:
        paper = screened.paper
        prompt = self._prompts.load(PROMPT_NAME)
        user = self._render_within_budget(prompt, paper)

        return await complete_structured(
            self._llm,
            CompletionRequest(
                system=SYSTEM,
                user=user,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stage=STAGE,
                prompt_version=prompt.version,
            ),
            PaperAnalysis,
            post_validate=lambda analysis: self._verify(analysis, paper),
        )

    # --- input ---------------------------------------------------------------

    def _render_within_budget(self, prompt: Prompt, paper: Paper) -> str:
        rendered = self._render(prompt, paper, paper.abstract)
        if len(rendered) <= self._max_input_chars:
            return rendered

        excess = len(rendered) - self._max_input_chars + len(TRUNCATION_MARKER)
        keep = max(0, len(paper.abstract) - excess)
        self.truncations += 1
        logger.warning(
            "analyzer input truncated",
            extra={"paper_id": paper.paper_id, "abstract_chars_kept": keep},
        )
        trimmed = self._render(prompt, paper, paper.abstract[:keep] + TRUNCATION_MARKER)
        return trimmed[: self._max_input_chars]

    def _render(self, prompt: Prompt, paper: Paper, abstract: str) -> str:
        return prompt.render(
            topic=self._topic,
            lang=self._lang,
            paper_id=paper.paper_id,
            title=paper.title,
            authors=", ".join(paper.authors),
            year=str(paper.year) if paper.year is not None else "n.d.",
            venue=paper.venue or "unknown venue",
            abstract=abstract,
        )

    # --- verification --------------------------------------------------------

    def _verify(self, analysis: PaperAnalysis, paper: Paper) -> PaperAnalysis:
        if analysis.paper_id != paper.paper_id:
            raise AnalysisFailedError(
                paper_ids=[paper.paper_id],
                reason="the analysis is for a different paper",
                detail=f"model returned paper_id {analysis.paper_id!r}",
            )

        haystack = build_haystack(paper)
        unsupported = [
            evidence.quote
            for evidence in analysis.evidence
            if not quote_is_supported(evidence.quote, haystack)
        ]
        if unsupported:
            raise ValueError(
                "These quotes do not appear in the abstract or metadata you were given:\n"
                + "\n".join(f"- {quote!r}" for quote in unsupported)
                + "\nCopy each quote verbatim from the supplied material, or use fewer quotes."
            )
        return self._shorten_quotes(analysis)

    def _shorten_quotes(self, analysis: PaperAnalysis) -> PaperAnalysis:
        """Bring every quote inside the report's display limit.

        A quote that runs long is a formatting overflow, not a factual problem,
        so it is trimmed instead of failing the paper — the trimmed form is
        still a verbatim prefix of the source.
        """
        if all(len(evidence.quote) <= MAX_QUOTE_CHARS for evidence in analysis.evidence):
            return analysis

        shortened = [
            evidence.model_copy(update={"quote": shorten_quote(evidence.quote)})
            for evidence in analysis.evidence
        ]
        self.shortened_quotes += 1
        logger.info(
            "shortened over-long evidence quotes",
            extra={"paper_id": analysis.paper_id, "limit": MAX_QUOTE_CHARS},
        )
        return analysis.model_copy(update={"evidence": shortened})


SYSTEM = "You are a careful paper analyst. You reply with a single JSON object and nothing else."


async def analyze_all(
    analyzer: Analyzer,
    papers: Sequence[ScreenedPaper],
    *,
    max_concurrency: int = 4,
    semaphore: asyncio.Semaphore | None = None,
) -> list[PaperAnalysis]:
    """Analyse every paper in parallel, preserving the input order.

    The semaphore bounds how many LLM calls are in flight at once
    (docs/architecture.md §6.1). Pass a shared one to cap the whole run;
    otherwise one is created from ``max_concurrency``.
    """
    gate = semaphore or asyncio.Semaphore(max_concurrency)

    async def analyze_one(screened: ScreenedPaper) -> PaperAnalysis:
        async with gate:
            return await analyzer.run(screened)

    results = await asyncio.gather(
        *(analyze_one(screened) for screened in papers),
        return_exceptions=True,
    )

    failures = [result for result in results if isinstance(result, BaseException)]
    if failures:
        failed_ids = [
            screened.paper.paper_id
            for screened, result in zip(papers, results, strict=True)
            if isinstance(result, BaseException)
        ]
        raise AnalysisFailedError(
            paper_ids=failed_ids,
            reason=f"{len(failures)} of {len(papers)} papers could not be analysed",
            detail="; ".join(str(failure) for failure in failures),
        )

    return [result for result in results if isinstance(result, PaperAnalysis)]

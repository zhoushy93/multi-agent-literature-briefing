"""S6 Synthesizer: turn selected papers and their analyses into a briefing.

Citation keys are the load-bearing part of this stage. The code assigns
``P1``..``Pn`` in ``SelectedPapers`` order, hands that closed set to the model,
and rejects any reply that cites a key outside it. Because the set is closed,
the renderer can later map ``Pk -> [k]`` deterministically and a briefing can
never point at a paper that was not selected (docs/architecture.md §3.3).

Two rules are deliberately **not** enforced here: the executive summary length
and the one-row-per-paper requirement. Both are Verifier L1 rules, and the
revision loop exists to repair what L1 reports (docs/architecture.md §4, S7).
Enforcing them here as well would mean L1 never sees a violation in a normal
run, so the revision path would be dead code — and with semantic verification
off by default, the whole loop would be unreachable.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from briefing.errors import BriefingError
from briefing.llm.base import (
    DEFAULT_MAX_TOKENS,
    CompletionRequest,
    LLMClient,
    complete_structured,
)
from briefing.prompts.loader import PromptLoader
from briefing.rendering import render_catalogue
from briefing.schemas import (
    MAX_SUMMARY_CHARS,
    Briefing,
    BriefingDraft,
    PaperAnalysis,
    PaperCard,
    SelectedPapers,
    TopicRequest,
    Violation,
    citation_key,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "synthesizer_v2"
STAGE = "S6"
TEMPERATURE = 0.3

# Rough CJK range: enough to decide the default report language (AGENTS.md §8).
_CJK_START = "\u3400"
_CJK_END = "\u9fff"


def detect_language(topic: str) -> Literal["zh", "en"]:
    """AGENTS.md §8: the report language defaults to the topic's language."""
    return "zh" if any(_CJK_START <= character <= _CJK_END for character in topic) else "en"


_SENTENCE_ENDS = ("。", "！", "？", ".", "；", ";", "，", ",")


def shorten_summary(summary: str, limit: int) -> str:
    """Bring the executive summary inside the report's limit.

    The model overshoots the limit now and then. Asking again costs a whole
    revision and may not help, so an over-long summary — a formatting overflow,
    not a factual error — is trimmed here, at a sentence or word boundary.
    The verifier still checks the limit as a safety net for other producers.
    """
    text = " ".join(summary.split())
    if len(text) <= limit:
        return text

    window = text[: max(1, limit - 1)]
    for marker in _SENTENCE_ENDS:
        cut = window.rfind(marker)
        if cut >= limit // 2:
            return window[: cut + 1].rstrip()
    cut = window.rfind(" ")
    if cut >= limit // 3:
        window = window[:cut]
    return f"{window.rstrip()}…"


class Synthesizer:
    """S6: produces a ``Briefing``, optionally repairing a rejected draft."""

    name = "synthesizer"

    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: PromptLoader | None = None,
        temperature: float = TEMPERATURE,
        summary_char_limit: int = MAX_SUMMARY_CHARS,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._llm = llm
        self._prompts = prompts or PromptLoader()
        self._temperature = temperature
        self._summary_char_limit = summary_char_limit
        self._max_tokens = max_tokens
        self.shortened_summaries = 0

    async def run(
        self,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
        request: TopicRequest,
        *,
        violations: Sequence[Violation] = (),
    ) -> Briefing:
        keys = self._validate_inputs(selected, analyses)
        lang = request.lang or detect_language(request.topic)
        prompt = self._prompts.load(PROMPT_NAME)

        user = prompt.render(
            run_id=request.run_id,
            topic=request.topic,
            lang=lang,
            summary_char_limit=str(self._summary_char_limit),
            catalogue=render_catalogue(selected, analyses, keys),
            violations=render_violations(violations, keys),
        )

        draft = await complete_structured(
            self._llm,
            CompletionRequest(
                system=SYSTEM,
                user=user,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
                stage=STAGE,
                prompt_version=prompt.version,
            ),
            BriefingDraft,
            # The key check runs inside the retry loop, so an invented key is
            # repaired by the model rather than failing the run.
            post_validate=lambda parsed: self._check_keys(parsed, allowed=set(keys)),
        )
        return self._assemble(
            draft,
            keys=keys,
            analyses=analyses,
            run_id=request.run_id,
            topic=request.topic,
            lang=lang,
        )

    # --- inputs --------------------------------------------------------------

    def _validate_inputs(
        self,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
    ) -> list[str]:
        if len(analyses) != len(selected.items):
            raise BriefingError(
                f"got {len(analyses)} analyses for {len(selected.items)} selected papers"
            )
        for position, (screened, analysis) in enumerate(
            zip(selected.items, analyses, strict=True),
            start=1,
        ):
            if analysis.paper_id != screened.paper.paper_id:
                raise BriefingError(
                    f"analysis {position} is for {analysis.paper_id!r} but the "
                    f"selected paper is {screened.paper.paper_id!r}"
                )
        return [citation_key(position) for position in range(1, len(selected.items) + 1)]

    # --- output --------------------------------------------------------------

    def _check_keys(self, draft: BriefingDraft, *, allowed: set[str]) -> BriefingDraft:
        unknown = sorted(self._collect_keys(draft) - allowed)
        if unknown:
            raise ValueError(
                "These citation keys do not exist: "
                + ", ".join(unknown)
                + "\nUse only these keys: "
                + ", ".join(sorted(allowed))
            )
        return draft

    def _assemble(
        self,
        draft: BriefingDraft,
        *,
        keys: Sequence[str],
        analyses: Sequence[PaperAnalysis],
        run_id: str,
        topic: str,
        lang: Literal["zh", "en"],
    ) -> Briefing:
        # The cards carry the analyses S5 already verified; the model never
        # reproduces them, so their structure cannot be flattened or altered.
        cards = [
            PaperCard(citation_key=key, analysis=analysis)
            for key, analysis in zip(keys, analyses, strict=True)
        ]
        summary = shorten_summary(draft.executive_summary, self._summary_char_limit)
        if summary != draft.executive_summary:
            self.shortened_summaries += 1
            logger.info(
                "shortened an over-long executive summary",
                extra={"limit": self._summary_char_limit},
            )
        return Briefing(
            paper_cards=cards,
            # run_id, topic and lang are run facts, not model output.
            run_id=run_id,
            topic=topic,
            lang=lang,
            executive_summary=summary,
            background_md=draft.background_md,
            method_md=draft.method_md,
            comparison=draft.comparison,
            gaps_and_open_questions=draft.gaps_and_open_questions,
            further_reading=draft.further_reading,
        )

    def _collect_keys(self, draft: BriefingDraft) -> set[str]:
        keys = {row.citation_key for row in draft.comparison}
        for claim in (*draft.gaps_and_open_questions, *draft.further_reading):
            keys.update(claim.citation_keys)
        return keys


SYSTEM = "You are a research synthesizer. You reply with a single JSON object and nothing else."


def render_violations(violations: Sequence[Violation], keys: Sequence[str]) -> str:
    """Turn verifier findings into repair instructions for the next draft."""
    if not violations:
        return "There is no earlier draft. This is the first attempt."

    lines = [
        "A previous draft was rejected by the verifier.",
        "Fix exactly these problems and leave everything else that is already correct alone:",
    ]
    lines.extend(
        f"- [{violation.severity}] {violation.kind} at {violation.location}: {violation.detail}"
        for violation in violations
    )
    lines.append("Every citation key must be one of: " + ", ".join(keys) + ".")
    return "\n".join(lines)

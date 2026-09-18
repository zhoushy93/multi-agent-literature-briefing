"""S7 L2: the semantic half of the verifier.

The model judges whether the papers cited by a claim actually support it. It
never edits the briefing: its reply schema carries findings only, so repairing
stays with the synthesizer (docs/architecture.md §4, S7).

Two guards keep the layer honest:

* the claim list is closed — a finding that points anywhere else is dropped;
* only ``unsupported_claim`` is L2's business. Anything else it reports belongs
  to L1, which has already run, so it is dropped and logged rather than
  silently accepted as a fresh opinion.

This layer is optional: ``BRIEFING_VERIFY_SEMANTIC`` defaults to off, so an
offline run never needs it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Literal

from briefing.config import Settings
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
    Briefing,
    PaperAnalysis,
    SelectedPapers,
    SemanticVerification,
    Violation,
    citation_keys,
)

logger = logging.getLogger(__name__)

PROMPT_NAME = "verifier_v1"
STAGE = "S7"

# AGENTS.md §6: judging support is extraction work, so it runs cold.
TEMPERATURE = 0.0

# L2 is only allowed to report this kind.
SEMANTIC_KIND: Literal["unsupported_claim"] = "unsupported_claim"


class ClaimRef:
    """A claim handed to the model, with the id it must cite back."""

    __slots__ = ("citation_keys", "location", "text")

    def __init__(self, location: str, citation_keys: Sequence[str], text: str) -> None:
        self.location = location
        self.citation_keys = list(citation_keys)
        self.text = text

    def render(self) -> str:
        keys = ", ".join(self.citation_keys) if self.citation_keys else "no key"
        return f"[{self.location}] (cites {keys}): {self.text}"


def collect_claims(briefing: Briefing) -> list[ClaimRef]:
    """Every claim that carries a citation key, in a stable order."""
    claims: list[ClaimRef] = []
    for index, claim in enumerate(briefing.gaps_and_open_questions):
        claims.append(
            ClaimRef(f"gaps_and_open_questions[{index}]", claim.citation_keys, claim.text)
        )
    for index, claim in enumerate(briefing.further_reading):
        claims.append(ClaimRef(f"further_reading[{index}]", claim.citation_keys, claim.text))
    for card_index, card in enumerate(briefing.paper_cards):
        for finding_index, finding in enumerate(card.analysis.key_findings):
            claims.append(
                ClaimRef(
                    f"paper_cards[{card_index}].analysis.key_findings[{finding_index}]",
                    [card.citation_key],
                    finding,
                )
            )
    return claims


class SemanticVerifier:
    """S7 L2: returns findings, never a rewritten briefing."""

    name = "semantic_verifier"

    def __init__(
        self,
        *,
        llm: LLMClient,
        prompts: PromptLoader | None = None,
        temperature: float = TEMPERATURE,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self._llm = llm
        self._prompts = prompts or PromptLoader()
        self._temperature = temperature
        self._max_tokens = max_tokens
        # Findings that were dropped because they pointed outside L2's remit.
        self.dropped: list[str] = []

    async def run(
        self,
        briefing: Briefing,
        selected: SelectedPapers,
        analyses: Sequence[PaperAnalysis],
    ) -> list[Violation]:
        self.dropped = []
        claims = collect_claims(briefing)
        if not claims:
            return []

        if len(analyses) != len(selected.items):
            raise BriefingError(
                f"got {len(analyses)} analyses for {len(selected.items)} selected papers"
            )

        keys = citation_keys(len(selected.items))
        prompt = self._prompts.load(PROMPT_NAME)
        user = prompt.render(
            catalogue=render_catalogue(selected, analyses, keys, include_abstract=True),
            claims="\n".join(claim.render() for claim in claims),
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
            SemanticVerification,
        )
        return self._filter(response.violations, {claim.location for claim in claims})

    def _filter(
        self,
        violations: Sequence[Violation],
        known_locations: set[str],
    ) -> list[Violation]:
        kept: list[Violation] = []
        for violation in violations:
            if violation.kind != SEMANTIC_KIND:
                self._drop(f"{violation.kind} at {violation.location} is not a semantic finding")
                continue
            if violation.location not in known_locations:
                self._drop(f"{violation.location} is not a claim that was audited")
                continue
            kept.append(violation)
        return kept

    def _drop(self, reason: str) -> None:
        self.dropped.append(reason)
        logger.warning("semantic finding dropped", extra={"reason": reason})


SYSTEM = (
    "You audit whether cited papers support a claim. "
    "You reply with a single JSON object and nothing else."
)


def create_semantic_verifier(
    settings: Settings,
    llm: LLMClient | None,
) -> SemanticVerifier | None:
    """Build L2 only when it is switched on and a client is available."""
    if not settings.verify_semantic or llm is None:
        return None
    return SemanticVerifier(
        llm=llm,
        temperature=TEMPERATURE,
        max_tokens=settings.max_output_tokens,
    )

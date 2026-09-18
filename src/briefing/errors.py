"""Exception hierarchy.

AGENTS.md §9: everything the CLI reports derives from ``BriefingError`` so the
entry point can catch one type and map it to a readable message plus an exit
code.
"""

from __future__ import annotations

from collections.abc import Sequence


class BriefingError(Exception):
    """Base class for all expected failures."""


class ConfigError(BriefingError):
    """The environment is missing something required to run."""


class BudgetExceeded(BriefingError):
    """A run hit MAX_LLM_CALLS or MAX_TOKENS_BUDGET (AGENTS.md §11)."""


class LLMRequestError(BriefingError):
    """The API rejected the request; retrying cannot help (4xx except 429)."""


class LLMTransientError(BriefingError):
    """A retryable failure: 429, 5xx, or a transport timeout."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class SchemaValidationError(BriefingError):
    """Structured output still did not match its contract after retries."""


class StubExhaustedError(BriefingError):
    """The offline stub ran out of recorded responses."""


class SourceError(BriefingError):
    """A paper source failed or returned something unusable."""


class PromptError(BriefingError):
    """A prompt file is missing, malformed, or unusable."""


class RenderError(BriefingError):
    """No PDF engine produced a usable report."""


class VerificationFailedError(BriefingError):
    """Fatal findings survived the one allowed revision (agent_architecture §5)."""


class InsufficientPapersError(BriefingError):
    """Fewer than ``min_papers`` usable papers survived screening.

    AGENTS.md §1.1: the run fails loudly with the evidence needed to widen the
    search, rather than padding the briefing with whatever it could find.
    """

    def __init__(
        self,
        *,
        found: int,
        required: int,
        candidate_count: int,
        queries: Sequence[str] = (),
        suggestions: Sequence[str] = (),
    ) -> None:
        self.found = found
        self.required = required
        self.candidate_count = candidate_count
        self.queries = list(queries)
        self.suggestions = list(suggestions)
        super().__init__(
            f"only {found} usable paper(s) but {required} required "
            f"(candidates offered: {candidate_count}); "
            f"queries used: {'; '.join(self.queries) if self.queries else 'none'}; "
            f"suggestions: {'; '.join(self.suggestions) if self.suggestions else 'none'}"
        )


class AnalysisFailedError(BriefingError):
    """A paper could not be analysed, so the run stops (AGENTS.md §1.1).

    AGENTS.md forbids silently dropping a paper: a briefing that claims to
    cover five papers while analysing four is worse than no briefing.
    """

    def __init__(
        self,
        *,
        paper_ids: Sequence[str],
        reason: str,
        detail: str = "",
    ) -> None:
        self.paper_ids = list(paper_ids)
        self.reason = reason
        self.detail = detail
        subject = ", ".join(self.paper_ids) if self.paper_ids else "unknown paper"
        message = f"paper analysis failed for {subject}: {reason}"
        if detail:
            message = f"{message} ({detail})"
        super().__init__(message)

"""Data-source boundary (docs/architecture.md §4, S2).

Adding a source means implementing ``Source`` and registering it; the Retriever
must not grow ``if source == ...`` branches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from briefing.schemas import Paper

SortBy = Literal["relevance", "submittedDate", "lastUpdatedDate"]

# AGENTS.md §7: one request may not ask for more than 100 records.
MAX_RESULTS_PER_REQUEST = 100

DEFAULT_MAX_RESULTS = 50

# AGENTS.md §7: identify the client on every request.
USER_AGENT = "briefing/0.1 (+https://github.com/example/briefing)"


@dataclass(frozen=True)
class FetchParams:
    """Per-query retrieval parameters. Part of the cache key."""

    max_results: int = DEFAULT_MAX_RESULTS
    start: int = 0
    sort_by: SortBy = "relevance"
    time_window: tuple[int | None, int | None] | None = None

    @property
    def capped_max_results(self) -> int:
        """Clamp to the per-request ceiling instead of letting the API truncate."""
        return max(1, min(self.max_results, MAX_RESULTS_PER_REQUEST))


class Source(Protocol):
    """A paper source. Implementations own their transport and parsing."""

    name: str

    async def search(self, query: str, params: FetchParams) -> list[Paper]: ...

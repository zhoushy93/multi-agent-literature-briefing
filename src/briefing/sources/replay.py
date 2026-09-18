"""Replay a recorded source response instead of calling the network.

``BRIEFING_LLM_MODE=stub`` makes a run fully offline: the model replays from
``BRIEFING_FIXTURES_DIR`` and so do the sources, which read the recorded Atom
XML sitting in the same directory. Parsing goes through the very same
``parse_atom`` as the live path, so a replay exercises the real code.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path

from briefing.errors import SourceError
from briefing.schemas import Paper
from briefing.sources.arxiv import parse_atom
from briefing.sources.base import FetchParams

logger = logging.getLogger(__name__)

RECORDING_PATTERN = "*.xml"


class FixtureSource:
    """Reads recorded responses from a directory, in filename order."""

    name = "arxiv"

    def __init__(
        self,
        *,
        fixtures_dir: Path,
        pattern: str = RECORDING_PATTERN,
    ) -> None:
        self._fixtures_dir = fixtures_dir
        self._pattern = pattern
        self.dropped: dict[str, int] = {}

    async def search(self, query: str, params: FetchParams) -> list[Paper]:
        recordings = sorted(self._fixtures_dir.glob(self._pattern))
        if not recordings:
            raise SourceError(
                f"no recorded source response in {self._fixtures_dir} (looked for {self._pattern})"
            )

        papers: list[Paper] = []
        dropped: dict[str, int] = {}
        for path in recordings:
            recorded = path.stat().st_mtime
            parsed, reasons = parse_atom(
                path.read_text(encoding="utf-8"),
                retrieved_at=datetime.fromtimestamp(recorded, tz=UTC),
                origin=self.name,
            )
            papers.extend(parsed)
            for reason, count in reasons.items():
                dropped[reason] = dropped.get(reason, 0) + count

        logger.info(
            "replayed a recorded source response",
            extra={"source": self.name, "files": len(recordings)},
        )
        self.dropped = dropped
        return papers

"""Raw-response cache (AGENTS.md §7, docs/architecture.md §3.1).

Envelopes live at ``<root>/<source>/<sha256(source, query, params)>.json`` and
carry the fetch timestamp so reuse is bounded by a TTL. ``--no-cache`` turns
the store off entirely: nothing is read *and* nothing is written, so a
``--no-cache`` run cannot silently repopulate the cache it was told to ignore.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from briefing.schemas import CacheStats

DEFAULT_TTL_DAYS = 30


@dataclass(frozen=True)
class CacheEntry:
    """A stored raw response."""

    url: str
    fetched_at: datetime
    body: str


class CacheStore:
    """Reads and writes raw source responses, counting hits and misses."""

    def __init__(
        self,
        *,
        root: Path,
        ttl_days: int = DEFAULT_TTL_DAYS,
        enabled: bool = True,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._root = root
        self._ttl = timedelta(days=ttl_days)
        self._enabled = enabled
        self._clock = clock or (lambda: datetime.now(UTC))
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        return self._enabled

    def key(self, *, source: str, query: str, params: Mapping[str, Any]) -> str:
        """Stable cache key: independent of dict ordering, stable across runs."""
        canonical = json.dumps(
            {"source": source, "query": query, "params": params},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def path_for(self, source: str, key: str) -> Path:
        return self._root / source / f"{key}.json"

    def load(self, *, source: str, query: str, params: Mapping[str, Any]) -> CacheEntry | None:
        """Return a fresh entry, or ``None`` on miss, expiry, or corruption."""
        if not self._enabled:
            return None

        path = self.path_for(source, self.key(source=source, query=query, params=params))
        entry = self._read_entry(path)
        if entry is None or self._is_expired(entry):
            self._misses += 1
            return None

        self._hits += 1
        return entry

    def save(
        self,
        *,
        source: str,
        query: str,
        params: Mapping[str, Any],
        entry: CacheEntry,
    ) -> None:
        if not self._enabled:
            return
        path = self.path_for(source, self.key(source=source, query=query, params=params))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "url": entry.url,
                    "fetched_at": entry.fetched_at.isoformat(),
                    "body": entry.body,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    async def get_or_fetch(
        self,
        *,
        source: str,
        url: str,
        query: str,
        params: Mapping[str, Any],
        fetch: Callable[[], Awaitable[str]],
    ) -> CacheEntry:
        """Return the cached response, or fetch, store and return a fresh one."""
        cached = self.load(source=source, query=query, params=params)
        if cached is not None:
            return cached

        body = await fetch()
        entry = CacheEntry(url=url, fetched_at=self._clock(), body=body)
        self.save(source=source, query=query, params=params, entry=entry)
        return entry

    def stats(self) -> CacheStats:
        return CacheStats(
            hits=self._hits,
            misses=self._misses,
            bypassed=not self._enabled,
        )

    # --- internals ----------------------------------------------------------

    def _read_entry(self, path: Path) -> CacheEntry | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return CacheEntry(
                url=str(payload["url"]),
                fetched_at=datetime.fromisoformat(str(payload["fetched_at"])),
                body=str(payload["body"]),
            )
        except (OSError, ValueError, KeyError, TypeError):
            # A truncated or hand-edited cache file is a miss, never a crash.
            return None

    def _is_expired(self, entry: CacheEntry) -> bool:
        return self._clock() - entry.fetched_at > self._ttl

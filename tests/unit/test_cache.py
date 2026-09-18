"""Raw-response cache tests (AGENTS.md §7)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from briefing.schemas import CacheStats
from briefing.sources.cache import CacheEntry, CacheStore

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
QUERY = "all:diffusion"
PARAMS = {"search_query": QUERY, "max_results": "50"}


class FakeClock:
    """A clock we can move forward to exercise the TTL."""

    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def make_store(tmp_path: Path, **kwargs: object) -> tuple[CacheStore, FakeClock]:
    clock = FakeClock()
    params: dict[str, object] = {"root": tmp_path / "cache", "clock": clock}
    params.update(kwargs)
    return CacheStore(**params), clock  # type: ignore[arg-type]


async def fetch_once(counter: list[int], body: str = "<feed/>") -> str:
    counter.append(1)
    return body


def test_key_is_stable_and_order_independent(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    first = store.key(source="arxiv", query=QUERY, params=PARAMS)
    shuffled = store.key(source="arxiv", query=QUERY, params=dict(reversed(list(PARAMS.items()))))
    assert first == shuffled
    assert first == store.key(source="arxiv", query=QUERY, params=PARAMS)
    assert len(first) == 64


def test_key_changes_with_source_query_and_params(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    base = store.key(source="arxiv", query=QUERY, params=PARAMS)
    assert store.key(source="openalex", query=QUERY, params=PARAMS) != base
    assert store.key(source="arxiv", query="all:climate", params=PARAMS) != base
    assert store.key(source="arxiv", query=QUERY, params={**PARAMS, "max_results": "10"}) != base


def test_entries_land_under_the_documented_path(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    key = store.key(source="arxiv", query=QUERY, params=PARAMS)
    assert store.path_for("arxiv", key) == tmp_path / "cache" / "arxiv" / f"{key}.json"


async def test_miss_then_hit(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    counter: list[int] = []

    first = await store.get_or_fetch(
        source="arxiv",
        url="https://example.test",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter, "body-v1"),
    )
    second = await store.get_or_fetch(
        source="arxiv",
        url="https://example.test",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter, "body-v2"),
    )

    assert len(counter) == 1, "the second call must not hit the network"
    assert first.body == second.body == "body-v1"
    assert second.fetched_at == NOW
    assert store.stats() == CacheStats(hits=1, misses=1, bypassed=False)


async def test_entry_just_inside_the_ttl_is_still_a_hit(tmp_path: Path) -> None:
    store, clock = make_store(tmp_path, ttl_days=30)
    counter: list[int] = []
    await store.get_or_fetch(
        source="arxiv",
        url="u",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter),
    )
    clock.now = NOW + timedelta(days=29, hours=23)
    await store.get_or_fetch(
        source="arxiv",
        url="u",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter),
    )
    assert len(counter) == 1


async def test_expired_entry_is_refetched(tmp_path: Path) -> None:
    store, clock = make_store(tmp_path, ttl_days=30)
    counter: list[int] = []
    await store.get_or_fetch(
        source="arxiv",
        url="u",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter, "old"),
    )

    clock.now = NOW + timedelta(days=31)
    refreshed = await store.get_or_fetch(
        source="arxiv",
        url="u",
        query=QUERY,
        params=PARAMS,
        fetch=lambda: fetch_once(counter, "new"),
    )

    assert len(counter) == 2
    assert refreshed.body == "new"
    assert refreshed.fetched_at == clock.now
    stats = store.stats()
    assert (stats.hits, stats.misses) == (0, 2)


async def test_disabled_store_never_reads_or_writes(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path, enabled=False)
    counter: list[int] = []
    for _ in range(2):
        await store.get_or_fetch(
            source="arxiv",
            url="u",
            query=QUERY,
            params=PARAMS,
            fetch=lambda: fetch_once(counter),
        )
    assert len(counter) == 2, "every call must go to the network"
    assert list((tmp_path / "cache").rglob("*.json")) == []
    assert store.stats() == CacheStats(hits=0, misses=0, bypassed=True)


def test_corrupt_cache_file_is_a_miss(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    key = store.key(source="arxiv", query=QUERY, params=PARAMS)
    path = store.path_for("arxiv", key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert store.load(source="arxiv", query=QUERY, params=PARAMS) is None
    assert store.stats().misses == 1


def test_incomplete_cache_envelope_is_a_miss(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    key = store.key(source="arxiv", query=QUERY, params=PARAMS)
    path = store.path_for("arxiv", key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"url": "u"}), encoding="utf-8")

    assert store.load(source="arxiv", query=QUERY, params=PARAMS) is None


def test_saved_envelope_records_url_and_timestamp(tmp_path: Path) -> None:
    store, _ = make_store(tmp_path)
    entry = CacheEntry(url="https://example.test", fetched_at=NOW, body="<feed/>")
    store.save(source="arxiv", query=QUERY, params=PARAMS, entry=entry)

    key = store.key(source="arxiv", query=QUERY, params=PARAMS)
    payload = json.loads(store.path_for("arxiv", key).read_text(encoding="utf-8"))
    assert payload["url"] == "https://example.test"
    assert payload["body"] == "<feed/>"
    assert payload["fetched_at"] == NOW.isoformat()
    assert store.load(source="arxiv", query=QUERY, params=PARAMS) == entry

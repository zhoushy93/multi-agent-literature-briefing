"""arXiv source tests. Fixture-driven, zero network."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from briefing.errors import SourceError
from briefing.sources.arxiv import ARXIV_ENDPOINT, ArxivSource
from briefing.sources.base import MAX_RESULTS_PER_REQUEST, FetchParams
from briefing.sources.cache import CacheStore

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)


def fixture_text(name: str = "arxiv_diffusion.xml") -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def serve(body: str) -> respx.Route:
    return respx.get(ARXIV_ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            text=body,
            headers={"Content-Type": "application/atom+xml"},
        )
    )


@pytest.fixture
async def make_source(tmp_path: Path) -> Iterator[object]:
    """Factory that builds sources and closes their HTTP clients afterwards."""
    created: list[ArxivSource] = []

    def _make(
        *,
        enabled: bool = True,
        min_interval_s: float = 0.0,
        max_attempts: int = 4,
        sleep: object = None,
        monotonic: object = None,
    ) -> ArxivSource:
        source = ArxivSource(
            cache=CacheStore(root=tmp_path / "cache", enabled=enabled, clock=lambda: NOW),
            min_interval_s=min_interval_s,
            max_attempts=max_attempts,
            sleep=sleep,  # type: ignore[arg-type]
            monotonic=monotonic,  # type: ignore[arg-type]
        )
        created.append(source)
        return source

    yield _make

    for source in created:
        await source.aclose()


@respx.mock
async def test_parses_every_linkable_entry(make_source: object) -> None:
    serve(fixture_text())
    source = make_source()  # type: ignore[operator]
    papers = await source.search("all:diffusion", FetchParams())
    assert len(papers) == 4
    assert source.dropped == {"missing_url": 1}


@respx.mock
async def test_full_metadata_is_extracted(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    first = papers[0]
    assert first.paper_id == "arxiv:2401.01234"
    assert first.title == "Diffusion Models for Medium-Range Weather Forecasting"
    assert first.authors == ["Alice Author", "Bob Builder"]
    assert first.year == 2024
    assert first.venue == "Journal of Weather ML 12 (2024) 345-367"
    assert first.origin == "arxiv"
    assert first.open_access is True
    assert first.citation_count is None
    assert first.retrieved_at == NOW
    assert first.abstract.startswith("We introduce a conditional diffusion model")
    assert "\n" not in first.abstract


@respx.mock
async def test_doi_is_parsed_when_present(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[0].doi == "10.1234/jwm.2024.345"


@respx.mock
async def test_missing_doi_is_none(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[1].doi is None
    assert papers[1].venue == "arxiv", "a preprint without a journal ref falls back to the origin"


@respx.mock
async def test_no_abstract_is_stored_as_an_empty_string(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[3].abstract == ""


@respx.mock
async def test_url_from_response_is_used_verbatim(make_source: object) -> None:
    """URLs come from the payload, never rebuilt from the identifier."""
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[0].url == "https://arxiv.org/abs/2401.01234"
    assert papers[1].url == "https://arxiv.org/abs/2311.05555v3"
    assert papers[2].url == "http://arxiv.org/abs/2210.00001v2"


@respx.mock
async def test_entry_without_an_alternate_link_falls_back_to_the_id(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[2].arxiv_id == "2210.00001"
    assert papers[2].url == "http://arxiv.org/abs/2210.00001v2"


@respx.mock
async def test_arxiv_id_drops_the_version_suffix(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[1].arxiv_id == "2311.05555"
    assert papers[1].paper_id == "arxiv:2311.05555"


@respx.mock
async def test_multi_line_titles_are_flattened(make_source: object) -> None:
    serve(fixture_text())
    papers = await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert papers[1].title == "Score-Based Generative Models for Precipitation Nowcasting"


@respx.mock
async def test_empty_feed_returns_no_papers(make_source: object) -> None:
    serve(fixture_text("arxiv_no_results.xml"))
    source = make_source()  # type: ignore[operator]
    assert await source.search("all:zzzznomatch", FetchParams()) == []
    assert source.dropped == {}


@respx.mock
async def test_malformed_xml_raises_a_source_error(make_source: object) -> None:
    serve("<feed><entry>")
    with pytest.raises(SourceError, match="malformed XML"):
        await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]


@respx.mock
async def test_http_error_raises_a_source_error(make_source: object) -> None:
    respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(503))
    with pytest.raises(SourceError, match="503"):
        await make_source(max_attempts=1).search(  # type: ignore[operator]
            "all:diffusion", FetchParams()
        )


@respx.mock
async def test_second_search_is_served_from_the_cache(make_source: object) -> None:
    route = serve(fixture_text())
    source = make_source()  # type: ignore[operator]
    await source.search("all:diffusion", FetchParams())
    await source.search("all:diffusion", FetchParams())
    assert route.call_count == 1


@respx.mock
async def test_no_cache_mode_always_refetches(make_source: object, tmp_path: Path) -> None:
    route = serve(fixture_text())
    source = make_source(enabled=False)  # type: ignore[operator]
    await source.search("all:diffusion", FetchParams())
    await source.search("all:diffusion", FetchParams())
    assert route.call_count == 2
    assert list((tmp_path / "cache").rglob("*.json")) == []


@respx.mock
async def test_different_params_are_cached_separately(make_source: object) -> None:
    route = serve(fixture_text())
    source = make_source()  # type: ignore[operator]
    await source.search("all:diffusion", FetchParams(max_results=10))
    await source.search("all:diffusion", FetchParams(max_results=20))
    assert route.call_count == 2
    assert source.dropped is not None


@respx.mock
async def test_max_results_is_capped_without_losing_the_request(make_source: object) -> None:
    route = serve(fixture_text())
    await make_source().search("all:diffusion", FetchParams(max_results=500))  # type: ignore[operator]
    params = route.calls[0].request.url.params
    assert params["max_results"] == str(MAX_RESULTS_PER_REQUEST)
    assert params["search_query"] == "all:diffusion"
    assert params["sortBy"] == "relevance"
    assert params["sortOrder"] == "descending"


@respx.mock
async def test_user_agent_is_sent(make_source: object) -> None:
    route = serve(fixture_text())
    await make_source().search("all:diffusion", FetchParams())  # type: ignore[operator]
    assert route.calls[0].request.headers["user-agent"].startswith("briefing/")


@respx.mock
async def test_time_window_is_appended_to_the_query(make_source: object) -> None:
    route = serve(fixture_text())
    await make_source().search(  # type: ignore[operator]
        "all:diffusion", FetchParams(time_window=(2020, 2024))
    )
    query = route.calls[0].request.url.params["search_query"]
    assert query == "(all:diffusion) AND submittedDate:[202001010000 TO 202412312359]"


@respx.mock
async def test_open_ended_time_window_uses_the_default_bounds(make_source: object) -> None:
    route = serve(fixture_text())
    await make_source().search(  # type: ignore[operator]
        "all:diffusion", FetchParams(time_window=(None, None))
    )
    query = route.calls[0].request.url.params["search_query"]
    assert "submittedDate:[199101010000 TO 299912312359]" in query


# --- politeness: spacing and retries -----------------------------------------


async def no_sleep(seconds: float) -> None:
    return None


@respx.mock
async def test_rate_limit_is_retried_then_succeeds(make_source: object) -> None:
    """arXiv answers bursts with 406; that must not fail the query."""
    waited: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waited.append(seconds)

    route = respx.get(ARXIV_ENDPOINT).mock(
        side_effect=[
            httpx.Response(406),
            httpx.Response(200, text=fixture_text()),
        ]
    )
    source = make_source(sleep=fake_sleep)  # type: ignore[operator]
    papers = await source.search("all:diffusion", FetchParams())

    assert route.call_count == 2, "a 406 is retried, not surfaced"
    assert len(papers) == 4
    assert waited, "the retry must pause before trying again"


@respx.mock
async def test_persistent_rate_limiting_fails_with_a_clear_message(
    make_source: object,
) -> None:
    """One quick retry, then the breaker trips instead of hammering on."""
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(406))
    source = make_source(max_attempts=3, sleep=no_sleep)  # type: ignore[operator]
    with pytest.raises(SourceError, match="rate-limited"):
        await source.search("all:diffusion", FetchParams())
    assert route.call_count == 2, "a rate limit is retried once, not four times"
    assert source.rate_limited is True


@respx.mock
async def test_the_breaker_stops_asking_arxiv_for_the_rest_of_the_run(
    make_source: object,
) -> None:
    """After a rate limit, later queries must not spend attempts on arXiv."""
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(406))
    source = make_source(sleep=no_sleep)  # type: ignore[operator]

    for _ in range(3):
        with pytest.raises(SourceError):
            await source.search("all:diffusion", FetchParams())

    assert route.call_count == 2, "only the first query talks to arXiv"


@respx.mock
async def test_concurrent_queries_do_not_slip_past_the_breaker(
    make_source: object,
) -> None:
    """Queries already in flight when the breaker trips must not send either."""
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(406))
    source = make_source(sleep=no_sleep)  # type: ignore[operator]

    results = await asyncio.gather(
        *(source.search(f"all:topic{index}", FetchParams()) for index in range(4)),
        return_exceptions=True,
    )

    assert all(isinstance(result, SourceError) for result in results)
    assert route.call_count == 2, "the retry budget is spent once for the whole run"


@respx.mock
async def test_the_rate_limit_budget_is_run_wide(make_source: object) -> None:
    """Two queries must not cost four requests: the budget is shared."""
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(406))
    source = make_source(sleep=no_sleep)  # type: ignore[operator]

    for _ in range(4):
        with pytest.raises(SourceError):
            await source.search("all:diffusion", FetchParams())

    assert route.call_count == 2


@respx.mock
async def test_a_server_error_keeps_the_full_retry_budget(make_source: object) -> None:
    """A 503 is transient, so it is retried more than a rate limit is."""
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(503))
    source = make_source(max_attempts=3, sleep=no_sleep)  # type: ignore[operator]
    with pytest.raises(SourceError):
        await source.search("all:diffusion", FetchParams())
    assert route.call_count == 3
    assert source.rate_limited is False


@respx.mock
async def test_requests_are_spaced_out(make_source: object) -> None:
    """Two queries must not leave back to back: that is what caused the 406."""
    slept: list[float] = []
    clock = {"now": 100.0}

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    serve(fixture_text())
    source = make_source(  # type: ignore[operator]
        min_interval_s=3.0,
        sleep=fake_sleep,
        monotonic=lambda: clock["now"],
    )
    await source.search("all:diffusion", FetchParams(max_results=10))
    await source.search("all:diffusion", FetchParams(max_results=20))

    assert slept == [3.0], "the second request waits out the minimum interval"


@respx.mock
async def test_retry_after_header_is_honoured(make_source: object) -> None:
    slept: list[float] = []
    clock = {"now": 500.0}

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    respx.get(ARXIV_ENDPOINT).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "7"}),
            httpx.Response(200, text=fixture_text()),
        ]
    )
    source = make_source(  # type: ignore[operator]
        sleep=fake_sleep,
        monotonic=lambda: clock["now"],
    )
    await source.search("all:diffusion", FetchParams())
    assert slept == [7.0], "the server's Retry-After is honoured exactly once"


@respx.mock
async def test_a_client_error_is_not_retried(make_source: object) -> None:
    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(400))
    with pytest.raises(SourceError, match="400"):
        await make_source(sleep=no_sleep).search(  # type: ignore[operator]
            "all:diffusion", FetchParams()
        )
    assert route.call_count == 1


@respx.mock
async def test_a_network_error_is_retried(make_source: object) -> None:
    route = respx.get(ARXIV_ENDPOINT).mock(
        side_effect=[
            httpx.ConnectTimeout("boom"),
            httpx.Response(200, text=fixture_text()),
        ]
    )
    source = make_source(sleep=no_sleep)  # type: ignore[operator]
    papers = await source.search("all:diffusion", FetchParams())
    assert route.call_count == 2
    assert len(papers) == 4


@respx.mock
async def test_a_cooldown_is_shared_between_queries(make_source: object) -> None:
    """The breaker is shared: the second query does not ask arXiv again."""
    slept: list[float] = []
    clock = {"now": 1000.0}

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    route = respx.get(ARXIV_ENDPOINT).mock(return_value=httpx.Response(406))
    source = make_source(  # type: ignore[operator]
        sleep=fake_sleep,
        monotonic=lambda: clock["now"],
    )
    results = await asyncio.gather(
        source.search("all:diffusion", FetchParams(max_results=10)),
        source.search("all:diffusion", FetchParams(max_results=20)),
        return_exceptions=True,
    )

    assert all(isinstance(result, SourceError) for result in results)
    assert route.call_count == 2, "one query spends the attempts; the other skips"
    assert source.rate_limited is True
    assert slept, "the first query still pauses once before giving up"


async def test_only_one_request_is_in_flight_at_a_time(tmp_path: Path) -> None:
    """The terms of use allow a single connection at a time."""
    state = {"active": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.01)
        state["active"] -= 1
        return httpx.Response(200, text=fixture_text())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = ArxivSource(
            cache=CacheStore(root=tmp_path / "cache", enabled=False),
            http_client=client,
            min_interval_s=0.0,
        )
        await asyncio.gather(*(source.search(f"all:q{index}", FetchParams()) for index in range(5)))

    assert state["peak"] == 1, "requests must never overlap"


async def test_the_connection_pool_is_capped(tmp_path: Path) -> None:
    source = ArxivSource(cache=CacheStore(root=tmp_path / "cache", enabled=False))
    try:
        pool = source._http._transport._pool
        assert pool._max_connections == 1
    finally:
        await source.aclose()

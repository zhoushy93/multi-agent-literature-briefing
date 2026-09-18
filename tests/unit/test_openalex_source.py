"""OpenAlex source tests: fixture-driven, zero network."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from briefing.errors import SourceError
from briefing.sources.base import FetchParams
from briefing.sources.cache import CacheStore
from briefing.sources.openalex import (
    OPENALEX_ENDPOINT,
    OpenAlexSource,
    abstract_from_inverted_index,
    parse_works,
    strip_doi_prefix,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
NOW = datetime(2026, 9, 18, 9, 0, tzinfo=UTC)


def payload() -> dict:
    return json.loads((FIXTURES / "openalex_works.json").read_text(encoding="utf-8"))


def make_source(tmp_path: Path, **kwargs: object) -> OpenAlexSource:
    params: dict[str, object] = {
        "cache": CacheStore(root=tmp_path / "cache", enabled=False, clock=lambda: NOW),
    }
    params.update(kwargs)
    return OpenAlexSource(**params)  # type: ignore[arg-type]


# --- helpers -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1175/mwr3199.1", "10.1175/mwr3199.1"),
        ("doi:10.1/x", "10.1/x"),
        ("10.1/x", "10.1/x"),
    ],
)
def test_doi_prefix_is_stripped(raw: str, expected: str) -> None:
    assert strip_doi_prefix(raw) == expected


def test_abstract_is_rebuilt_from_the_inverted_index() -> None:
    index = {"Diffusion": [0], "models": [1], "work": [2], "well": [3]}
    assert abstract_from_inverted_index(index) == "Diffusion models work well"


def test_a_missing_index_is_an_empty_abstract() -> None:
    assert abstract_from_inverted_index(None) == ""
    assert abstract_from_inverted_index({}) == ""


# --- parsing -----------------------------------------------------------------


def test_recorded_works_are_parsed() -> None:
    papers, dropped = parse_works(payload(), retrieved_at=NOW, origin="openalex")
    assert len(papers) == 2
    assert dropped == {}

    first = papers[0]
    assert first.title.startswith("A New Vertical Diffusion Package")
    assert first.authors[:3] == ["Song-You Hong", "Yign Noh", "Jimy Dudhia"]
    assert first.year == 2006
    assert first.venue == "Monthly Weather Review"
    assert first.doi == "10.1175/mwr3199.1"
    assert first.paper_id == "doi:10.1175/mwr3199.1"
    assert first.origin == "openalex"
    assert first.retrieved_at == NOW
    assert first.url.startswith("http")
    assert "diffusion" in first.abstract.lower()


def test_a_work_without_a_title_is_dropped() -> None:
    data = payload()
    data["results"][0]["title"] = None
    papers, dropped = parse_works(data, retrieved_at=NOW, origin="openalex")
    assert len(papers) == 1
    assert dropped == {"missing_title": 1}


def test_a_work_without_authors_is_dropped() -> None:
    data = payload()
    data["results"][0]["authorships"] = []
    papers, dropped = parse_works(data, retrieved_at=NOW, origin="openalex")
    assert len(papers) == 1
    assert dropped == {"missing_authors": 1}


def test_a_work_without_an_identifier_still_gets_one_from_openalex() -> None:
    data = payload()
    data["results"][0]["doi"] = None
    papers, _ = parse_works(data, retrieved_at=NOW, origin="openalex")
    assert papers[0].paper_id.startswith("openalex:W")
    assert papers[0].doi is None


# --- requests ----------------------------------------------------------------


@respx.mock
async def test_search_parses_and_caches(tmp_path: Path) -> None:
    route = respx.get(OPENALEX_ENDPOINT).mock(return_value=httpx.Response(200, json=payload()))
    source = make_source(tmp_path)
    try:
        papers = await source.search("diffusion model weather", FetchParams())
        await source.search("diffusion model weather", FetchParams())
    finally:
        await source.aclose()

    assert len(papers) == 2
    assert route.call_count == 2, "cache disabled in this test"


@respx.mock
async def test_request_carries_search_page_and_mailto(tmp_path: Path) -> None:
    route = respx.get(OPENALEX_ENDPOINT).mock(return_value=httpx.Response(200, json=payload()))
    source = make_source(tmp_path, mailto="someone@example.org")
    try:
        await source.search(
            "diffusion model weather",
            FetchParams(max_results=500, time_window=(2020, 2024)),
        )
    finally:
        await source.aclose()

    params = route.calls[0].request.url.params
    assert params["search"] == "diffusion model weather"
    assert params["per-page"] == "100", "per-page is capped at MAX_PER_PAGE"
    assert params["mailto"] == "someone@example.org"
    assert params["filter"] == ("from_publication_date:2020-01-01,to_publication_date:2024-12-31")


@respx.mock
async def test_without_mailto_the_parameter_is_omitted(tmp_path: Path) -> None:
    route = respx.get(OPENALEX_ENDPOINT).mock(return_value=httpx.Response(200, json=payload()))
    source = make_source(tmp_path)
    try:
        await source.search("diffusion", FetchParams())
    finally:
        await source.aclose()
    assert "mailto" not in route.calls[0].request.url.params


@respx.mock
async def test_http_error_is_a_source_error(tmp_path: Path) -> None:
    respx.get(OPENALEX_ENDPOINT).mock(return_value=httpx.Response(503))
    source = make_source(tmp_path)
    try:
        with pytest.raises(SourceError, match="503"):
            await source.search("diffusion", FetchParams())
    finally:
        await source.aclose()


@respx.mock
async def test_malformed_json_is_a_source_error(tmp_path: Path) -> None:
    respx.get(OPENALEX_ENDPOINT).mock(
        return_value=httpx.Response(200, text="<html>not json</html>")
    )
    source = make_source(tmp_path)
    try:
        with pytest.raises(SourceError, match="malformed JSON"):
            await source.search("diffusion", FetchParams())
    finally:
        await source.aclose()
